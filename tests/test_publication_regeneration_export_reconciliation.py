"""Focused provider-free tests for Phase 275 export reconciliation."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ai_office.engine import (
    PublicationRegenerationExportReceipt,
    PublicationRegenerationExportReconciliation,
    PublicationRegenerationExportReconciliationError,
    PublicationRegenerationExportReconciliationFailureDetail,
    persist_publication_regeneration_export_receipt,
    publication_regeneration_export_receipt_digest,
    reconcile_publication_regeneration_export,
)
from ai_office.engine import (
    publication_regeneration_export_reconciliation as reconciliation_module,
)

_SCHEMA_VERSION = "publication-regeneration-export-reconciliation.v1"
_RECEIPT_SCHEMA_VERSION = "publication-regeneration-export-receipt.v1"
_OUTPUT = "ユニコ\n  \t\r\n".encode()


@pytest.fixture
def receipt() -> PublicationRegenerationExportReceipt:
    return _receipt_for(_OUTPUT)


def _receipt_for(
    output: bytes,
) -> PublicationRegenerationExportReceipt:
    return PublicationRegenerationExportReceipt(
        schema_version=_RECEIPT_SCHEMA_VERSION,
        regeneration_id="regen-275-日本語😀",
        projection_sha256="a" * 64,
        readiness_record_sha256="b" * 64,
        result_record_sha256="c" * 64,
        source_audit_sha256="d" * 64,
        business_output_sha256=hashlib.sha256(output).hexdigest(),
        output_byte_length=len(output),
    )


def _persist_and_write(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
    output: bytes,
) -> tuple[Path, Path]:
    receipt_path = tmp_path / "receipt.json"
    output_path = tmp_path / "business-output.txt"
    persist_publication_regeneration_export_receipt(receipt_path, receipt)
    output_path.write_bytes(output)
    return receipt_path, output_path


def _assert_fixed_error(
    error: PublicationRegenerationExportReconciliationError,
    classification: str,
) -> None:
    assert str(error) == "publication regeneration export reconciliation failed"
    assert isinstance(
        error.detail, PublicationRegenerationExportReconciliationFailureDetail
    )
    assert error.detail.classification == classification


def test_exact_receipt_and_exact_bytes_are_matched(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
) -> None:
    receipt_path, output_path = _persist_and_write(tmp_path, receipt, _OUTPUT)

    result = reconcile_publication_regeneration_export(
        receipt_path=receipt_path,
        output_path=output_path,
    )

    assert result == PublicationRegenerationExportReconciliation(
        schema_version=_SCHEMA_VERSION,
        regeneration_id=receipt.regeneration_id,
        receipt_sha256=publication_regeneration_export_receipt_digest(receipt),
        status="matched",
        expected_business_output_sha256=receipt.business_output_sha256,
        expected_output_byte_length=receipt.output_byte_length,
        observed_business_output_sha256=hashlib.sha256(_OUTPUT).hexdigest(),
        observed_output_byte_length=len(_OUTPUT),
    )
    assert result.__dataclass_params__.frozen is True


def test_empty_output_can_match_zero_length_receipt(tmp_path: Path) -> None:
    output = b""
    receipt = _receipt_for(output)
    receipt_path, output_path = _persist_and_write(tmp_path, receipt, output)

    result = reconcile_publication_regeneration_export(
        receipt_path=receipt_path,
        output_path=output_path,
    )

    assert result.status == "matched"
    assert result.expected_output_byte_length == 0
    assert result.observed_output_byte_length == 0
    assert result.expected_business_output_sha256 == hashlib.sha256(output).hexdigest()


def test_unicode_newline_and_whitespace_bytes_are_hashed_without_normalization(
    tmp_path: Path,
) -> None:
    output = "日本語\n  \t\r\n😀".encode()
    receipt = _receipt_for(output)
    receipt_path, output_path = _persist_and_write(tmp_path, receipt, output)

    result = reconcile_publication_regeneration_export(
        receipt_path=receipt_path,
        output_path=output_path,
    )

    assert result.status == "matched"
    assert result.observed_business_output_sha256 == hashlib.sha256(output).hexdigest()
    assert result.observed_output_byte_length == len(output)


def test_absent_output_is_a_normal_missing_result(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    output_path = tmp_path / "absent.txt"
    persist_publication_regeneration_export_receipt(receipt_path, receipt)

    result = reconcile_publication_regeneration_export(
        receipt_path=receipt_path,
        output_path=output_path,
    )

    assert result.status == "missing"
    assert result.observed_business_output_sha256 is None
    assert result.observed_output_byte_length is None


def test_same_length_different_bytes_are_content_mismatch(tmp_path: Path) -> None:
    expected = b"expected-contents"
    observed = b"private-contents!"
    assert len(expected) == len(observed)
    receipt_path, output_path = _persist_and_write(
        tmp_path, _receipt_for(expected), observed
    )

    result = reconcile_publication_regeneration_export(
        receipt_path=receipt_path,
        output_path=output_path,
    )

    assert result.status == "content_mismatch"
    assert (
        result.observed_business_output_sha256
        == hashlib.sha256(observed).hexdigest()
    )
    assert result.observed_output_byte_length == len(observed)
    assert not hasattr(result, "business_output_text")
    assert observed.decode() not in repr(result)


def test_different_length_bytes_are_content_mismatch(tmp_path: Path) -> None:
    expected = b"expected"
    observed = b"different-length"
    assert len(expected) != len(observed)
    receipt_path, output_path = _persist_and_write(
        tmp_path, _receipt_for(expected), observed
    )

    result = reconcile_publication_regeneration_export(
        receipt_path=receipt_path,
        output_path=output_path,
    )

    assert result.status == "content_mismatch"
    assert (
        result.observed_business_output_sha256
        == hashlib.sha256(observed).hexdigest()
    )
    assert result.observed_output_byte_length == len(observed)


def test_output_path_requires_exact_repository_path_type(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    persist_publication_regeneration_export_receipt(receipt_path, receipt)

    with pytest.raises(PublicationRegenerationExportReconciliationError) as raised:
        reconcile_publication_regeneration_export(
            receipt_path=receipt_path,
            output_path=str(tmp_path / "output.txt"),  # type: ignore[arg-type]
        )

    _assert_fixed_error(raised.value, "path_type")


def test_receipt_path_is_passed_unchanged_and_loader_is_called_once(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path = tmp_path / "caller-receipt.json"
    output_path = tmp_path / "output.txt"
    output_path.write_bytes(_OUTPUT)
    seen: list[object] = []

    def load(path: object) -> PublicationRegenerationExportReceipt:
        seen.append(path)
        return receipt

    monkeypatch.setattr(
        reconciliation_module,
        "load_publication_regeneration_export_receipt",
        load,
    )

    reconcile_publication_regeneration_export(
        receipt_path=receipt_path,
        output_path=output_path,
    )

    assert seen == [receipt_path]
    assert seen[0] is receipt_path


def test_receipt_digest_helper_binds_the_exact_loaded_receipt_once(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    output_path = tmp_path / "output.txt"
    output_path.write_bytes(_OUTPUT)
    loaded: list[object] = []
    digested: list[object] = []

    def load(path: Path) -> PublicationRegenerationExportReceipt:
        loaded.append(path)
        return receipt

    def digest(value: object) -> str:
        digested.append(value)
        return publication_regeneration_export_receipt_digest(receipt)

    monkeypatch.setattr(
        reconciliation_module,
        "load_publication_regeneration_export_receipt",
        load,
    )
    monkeypatch.setattr(
        reconciliation_module,
        "publication_regeneration_export_receipt_digest",
        digest,
    )

    result = reconcile_publication_regeneration_export(
        receipt_path=receipt_path,
        output_path=output_path,
    )

    assert loaded == [receipt_path]
    assert digested == [receipt]
    assert result.receipt_sha256 == publication_regeneration_export_receipt_digest(
        receipt
    )


def test_loader_error_is_sanitized_without_retry_or_output_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path = tmp_path / "secret-receipt-path.json"
    output_path = tmp_path / "secret-output-path.txt"
    calls = 0

    def load(path: object) -> PublicationRegenerationExportReceipt:
        nonlocal calls
        calls += 1
        raise RuntimeError(
            f"/secret/path {path} digest={'a' * 64} content=private-output"
        )

    monkeypatch.setattr(
        reconciliation_module,
        "load_publication_regeneration_export_receipt",
        load,
    )

    with pytest.raises(PublicationRegenerationExportReconciliationError) as raised:
        reconcile_publication_regeneration_export(
            receipt_path=receipt_path,
            output_path=output_path,
        )

    _assert_fixed_error(raised.value, "receipt")
    assert calls == 1
    assert "/secret/path" not in str(raised.value)
    assert "private-output" not in str(raised.value)
    assert "a" * 64 not in str(raised.value)


def test_digest_error_is_sanitized_without_retry(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    output_path = tmp_path / "output.txt"
    output_path.write_bytes(_OUTPUT)
    calls = 0

    monkeypatch.setattr(
        reconciliation_module,
        "load_publication_regeneration_export_receipt",
        lambda path: receipt,
    )

    def digest(value: object) -> str:
        nonlocal calls
        calls += 1
        raise RuntimeError("provider payload /secret/output digest=" + "b" * 64)

    monkeypatch.setattr(
        reconciliation_module,
        "publication_regeneration_export_receipt_digest",
        digest,
    )

    with pytest.raises(PublicationRegenerationExportReconciliationError) as raised:
        reconcile_publication_regeneration_export(
            receipt_path=receipt_path,
            output_path=output_path,
        )

    _assert_fixed_error(raised.value, "receipt")
    assert calls == 1
    assert "provider payload" not in str(raised.value)
    assert "b" * 64 not in str(raised.value)


def test_malformed_receipt_error_is_sanitized(tmp_path: Path) -> None:
    receipt_path = tmp_path / "malformed-receipt.json"
    output_path = tmp_path / "output.txt"
    receipt_path.write_text(
        '{"schema_version":"tampered","business_output_sha256":"secret"}',
        encoding="utf-8",
    )

    with pytest.raises(PublicationRegenerationExportReconciliationError) as raised:
        reconcile_publication_regeneration_export(
            receipt_path=receipt_path,
            output_path=output_path,
        )

    _assert_fixed_error(raised.value, "receipt")
    assert "tampered" not in str(raised.value)
    assert "secret" not in str(raised.value)
    assert str(receipt_path) not in str(raised.value)


@pytest.mark.parametrize(
    ("target_kind", "classification"),
    (("directory", "target"), ("symlink", "target")),
)
def test_directory_and_symlink_targets_are_rejected_safely(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
    target_kind: str,
    classification: str,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    persist_publication_regeneration_export_receipt(receipt_path, receipt)
    target = tmp_path / "target"
    if target_kind == "directory":
        target.mkdir()
    else:
        real = tmp_path / "real-output.txt"
        real.write_bytes(_OUTPUT)
        try:
            target.symlink_to(real)
        except OSError:
            pytest.skip("symlinks are unavailable")

    with pytest.raises(PublicationRegenerationExportReconciliationError) as raised:
        reconcile_publication_regeneration_export(
            receipt_path=receipt_path,
            output_path=target,
        )

    _assert_fixed_error(raised.value, classification)
    if target_kind == "symlink":
        assert (tmp_path / "real-output.txt").read_bytes() == _OUTPUT


def test_special_file_target_is_not_read(
    tmp_path: Path, receipt: PublicationRegenerationExportReceipt
) -> None:
    if not hasattr(__import__("os"), "mkfifo"):
        pytest.skip("special files are unavailable")
    import os

    receipt_path = tmp_path / "receipt.json"
    persist_publication_regeneration_export_receipt(receipt_path, receipt)
    target = tmp_path / "fifo"
    try:
        os.mkfifo(target)
    except OSError:
        pytest.skip("FIFO creation is unavailable")

    with pytest.raises(PublicationRegenerationExportReconciliationError) as raised:
        reconcile_publication_regeneration_export(
            receipt_path=receipt_path,
            output_path=target,
        )

    _assert_fixed_error(raised.value, "target")


def test_permission_or_generic_read_failure_is_not_missing(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    output_path = tmp_path / "output.txt"
    persist_publication_regeneration_export_receipt(receipt_path, receipt)
    output_path.write_bytes(_OUTPUT)
    real_read_bytes = Path.read_bytes

    def fail_read(path: Path) -> bytes:
        if path == output_path:
            raise PermissionError("private output bytes")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_read)

    with pytest.raises(PublicationRegenerationExportReconciliationError) as raised:
        reconcile_publication_regeneration_export(
            receipt_path=receipt_path,
            output_path=output_path,
        )

    _assert_fixed_error(raised.value, "read")
    assert "private output bytes" not in str(raised.value)


def test_output_content_is_read_at_most_once_and_mismatch_is_not_retried(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    output_path = tmp_path / "output.txt"
    output_path.write_bytes(b"wrong!!")
    reads: list[Path] = []
    real_read_bytes = Path.read_bytes

    monkeypatch.setattr(
        reconciliation_module,
        "load_publication_regeneration_export_receipt",
        lambda path: receipt,
    )

    def read_once(path: Path) -> bytes:
        if path == output_path:
            reads.append(path)
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_once)

    result = reconcile_publication_regeneration_export(
        receipt_path=receipt_path,
        output_path=output_path,
    )

    assert result.status == "content_mismatch"
    assert reads == [output_path]


def test_result_model_rejects_inconsistent_status_invariants() -> None:
    common = {
        "schema_version": _SCHEMA_VERSION,
        "regeneration_id": "regen-275",
        "receipt_sha256": "a" * 64,
        "expected_business_output_sha256": "b" * 64,
        "expected_output_byte_length": 3,
    }
    invalid = (
        {
            **common,
            "status": "matched",
            "observed_business_output_sha256": "c" * 64,
            "observed_output_byte_length": 4,
        },
        {
            **common,
            "status": "missing",
            "observed_business_output_sha256": "c" * 64,
            "observed_output_byte_length": None,
        },
        {
            **common,
            "status": "content_mismatch",
            "observed_business_output_sha256": "b" * 64,
            "observed_output_byte_length": 3,
        },
    )

    for values in invalid:
        with pytest.raises(PublicationRegenerationExportReconciliationError) as raised:
            PublicationRegenerationExportReconciliation(**values)  # type: ignore[arg-type]
        _assert_fixed_error(raised.value, "result")


@pytest.mark.parametrize(
    "field_values",
    (
        {"schema_version": "wrong"},
        {"regeneration_id": ""},
        {"receipt_sha256": "A" * 64},
        {"receipt_sha256": "not-a-digest"},
        {"status": "unknown"},
        {"expected_business_output_sha256": "f" * 63},
        {"expected_output_byte_length": True},
        {"expected_output_byte_length": -1},
        {"observed_business_output_sha256": "G" * 64},
        {"observed_output_byte_length": False},
        {"observed_output_byte_length": -1},
    ),
)
def test_result_model_rejects_invalid_schema_status_digest_and_lengths(
    field_values: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "regeneration_id": "regen-275",
        "receipt_sha256": "a" * 64,
        "status": "matched",
        "expected_business_output_sha256": "b" * 64,
        "expected_output_byte_length": 3,
        "observed_business_output_sha256": "b" * 64,
        "observed_output_byte_length": 3,
    }
    values.update(field_values)

    with pytest.raises(PublicationRegenerationExportReconciliationError) as raised:
        PublicationRegenerationExportReconciliation(**values)  # type: ignore[arg-type]

    _assert_fixed_error(raised.value, "result")


def test_result_model_is_frozen() -> None:
    result = PublicationRegenerationExportReconciliation(
        schema_version=_SCHEMA_VERSION,
        regeneration_id="regen-275",
        receipt_sha256="a" * 64,
        status="missing",
        expected_business_output_sha256="b" * 64,
        expected_output_byte_length=0,
        observed_business_output_sha256=None,
        observed_output_byte_length=None,
    )

    with pytest.raises((AttributeError, TypeError)):
        result.status = "matched"  # type: ignore[misc]


def test_receipt_and_output_are_unchanged_for_match_and_mismatch(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
) -> None:
    receipt_path, output_path = _persist_and_write(tmp_path, receipt, _OUTPUT)
    receipt_before = receipt_path.read_bytes()
    output_before = output_path.read_bytes()

    matched = reconcile_publication_regeneration_export(
        receipt_path=receipt_path,
        output_path=output_path,
    )
    output_path.write_bytes(b"changed-but-readable")
    mismatch = reconcile_publication_regeneration_export(
        receipt_path=receipt_path,
        output_path=output_path,
    )

    assert matched.status == "matched"
    assert mismatch.status == "content_mismatch"
    assert receipt_path.read_bytes() == receipt_before
    assert output_path.read_bytes() == b"changed-but-readable"
    assert output_before == _OUTPUT


def test_all_other_lineage_artifacts_remain_unchanged(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
) -> None:
    lineage = tmp_path / "lineage"
    lineage.mkdir()
    artifacts = {
        "state.json": b"state",
        "events.jsonl": b"events\n",
        "result.json": b"result",
        "readiness.json": b"readiness",
        "audit.json": b"audit",
        "claim.json": b"claim",
        "projection.json": b"projection",
    }
    for name, contents in artifacts.items():
        (lineage / name).write_bytes(contents)
    receipt_path = lineage / "receipt.json"
    output_path = lineage / "business-output.txt"
    persist_publication_regeneration_export_receipt(receipt_path, receipt)
    output_path.write_bytes(_OUTPUT)
    before = {name: (lineage / name).read_bytes() for name in artifacts}
    receipt_before = receipt_path.read_bytes()
    output_before = output_path.read_bytes()

    reconcile_publication_regeneration_export(
        receipt_path=receipt_path,
        output_path=output_path,
    )

    assert {name: (lineage / name).read_bytes() for name in artifacts} == before
    assert receipt_path.read_bytes() == receipt_before
    assert output_path.read_bytes() == output_before


def test_public_engine_exports_are_available() -> None:
    import ai_office.engine as engine

    for name in (
        "PublicationRegenerationExportReconciliation",
        "PublicationRegenerationExportReconciliationError",
        "PublicationRegenerationExportReconciliationFailureDetail",
        "reconcile_publication_regeneration_export",
    ):
        assert hasattr(engine, name)


def test_no_write_side_effects_are_attempted(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path, output_path = _persist_and_write(tmp_path, receipt, _OUTPUT)
    real_open = Path.open

    def guarded_open(path: Path, mode: str = "r", *args: object, **kwargs: object):
        assert not any(flag in mode for flag in ("w", "a", "x", "+"))
        return real_open(path, mode, *args, **kwargs)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("reconciliation attempted filesystem mutation")

    monkeypatch.setattr(Path, "open", guarded_open)
    for name in (
        "write_bytes",
        "write_text",
        "unlink",
        "rename",
        "replace",
        "mkdir",
        "chmod",
    ):
        monkeypatch.setattr(Path, name, forbidden)

    reconcile_publication_regeneration_export(
        receipt_path=receipt_path,
        output_path=output_path,
    )


def test_unexpected_loader_return_type_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        reconciliation_module,
        "load_publication_regeneration_export_receipt",
        lambda path: {"not": "a receipt"},
    )

    with pytest.raises(PublicationRegenerationExportReconciliationError) as raised:
        reconcile_publication_regeneration_export(
            receipt_path=tmp_path / "receipt.json",
            output_path=tmp_path / "output.txt",
        )

    _assert_fixed_error(raised.value, "receipt")


def test_missing_receipt_path_error_is_fixed_and_does_not_leak_path(
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "missing-secret-receipt.json"

    with pytest.raises(PublicationRegenerationExportReconciliationError) as raised:
        reconcile_publication_regeneration_export(
            receipt_path=receipt_path,
            output_path=tmp_path / "output.txt",
        )

    _assert_fixed_error(raised.value, "receipt")
    assert str(receipt_path) not in str(raised.value)


def test_digest_and_observation_are_deterministic_without_clock_or_provider(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
) -> None:
    receipt_path, output_path = _persist_and_write(tmp_path, receipt, _OUTPUT)

    first = reconcile_publication_regeneration_export(
        receipt_path=receipt_path,
        output_path=output_path,
    )
    second = reconcile_publication_regeneration_export(
        receipt_path=receipt_path,
        output_path=output_path,
    )

    assert first == second
    assert first.receipt_sha256 == publication_regeneration_export_receipt_digest(
        receipt
    )
    assert first.observed_business_output_sha256 == hashlib.sha256(_OUTPUT).hexdigest()


def test_output_path_subclass_is_rejected_as_non_exact_type(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
) -> None:
    path_type = type(tmp_path)

    class PathChild(path_type):
        pass

    receipt_path = tmp_path / "receipt.json"
    persist_publication_regeneration_export_receipt(receipt_path, receipt)
    output_path = PathChild(str(tmp_path / "output.txt"))

    with pytest.raises(PublicationRegenerationExportReconciliationError) as raised:
        reconcile_publication_regeneration_export(
            receipt_path=receipt_path,
            output_path=output_path,
        )

    _assert_fixed_error(raised.value, "path_type")


def test_read_error_is_not_retried(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    output_path = tmp_path / "output.txt"
    persist_publication_regeneration_export_receipt(receipt_path, receipt)
    output_path.write_bytes(_OUTPUT)
    reads = 0
    real_read_bytes = Path.read_bytes

    def fail_once(path: Path) -> bytes:
        nonlocal reads
        if path == output_path:
            reads += 1
            raise OSError("internal output read failure")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_once)

    with pytest.raises(PublicationRegenerationExportReconciliationError) as raised:
        reconcile_publication_regeneration_export(
            receipt_path=receipt_path,
            output_path=output_path,
        )

    _assert_fixed_error(raised.value, "read")
    assert reads == 1
