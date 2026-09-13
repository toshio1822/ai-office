"""Focused provider-free tests for Phase 276 reconciliation evidence."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from pathlib import Path

import pytest

from ai_office.engine import (
    PublicationRegenerationExportReconciliation,
    PublicationRegenerationExportReconciliationEvidenceConflictError,
    PublicationRegenerationExportReconciliationEvidenceError,
    PublicationRegenerationExportReconciliationEvidenceLoadError,
    PublicationRegenerationExportReconciliationEvidencePersistenceError,
    load_publication_regeneration_export_reconciliation,
    persist_publication_regeneration_export_reconciliation,
    publication_regeneration_export_reconciliation_canonical_bytes,
    publication_regeneration_export_reconciliation_digest,
    serialize_publication_regeneration_export_reconciliation_canonical,
)
from ai_office.engine import (
    publication_regeneration_export_reconciliation_evidence as evidence_module,
)

_SCHEMA_VERSION = "publication-regeneration-export-reconciliation.v1"
_RECONCILIATION_KEYS = {
    "schema_version",
    "regeneration_id",
    "receipt_sha256",
    "status",
    "expected_business_output_sha256",
    "expected_output_byte_length",
    "observed_business_output_sha256",
    "observed_output_byte_length",
}
_EXPECTED_DIGEST = "a" * 64
_RECEIPT_DIGEST = "b" * 64
_OBSERVED_DIGEST = "c" * 64


@pytest.fixture
def matched() -> PublicationRegenerationExportReconciliation:
    return _matched()


def _matched() -> PublicationRegenerationExportReconciliation:
    return PublicationRegenerationExportReconciliation(
        schema_version=_SCHEMA_VERSION,
        regeneration_id="regen-276-日本語😀",
        receipt_sha256=_RECEIPT_DIGEST,
        status="matched",
        expected_business_output_sha256=_EXPECTED_DIGEST,
        expected_output_byte_length=17,
        observed_business_output_sha256=_EXPECTED_DIGEST,
        observed_output_byte_length=17,
    )


def _missing() -> PublicationRegenerationExportReconciliation:
    return PublicationRegenerationExportReconciliation(
        schema_version=_SCHEMA_VERSION,
        regeneration_id="regen-276-missing",
        receipt_sha256=_RECEIPT_DIGEST,
        status="missing",
        expected_business_output_sha256=_EXPECTED_DIGEST,
        expected_output_byte_length=17,
        observed_business_output_sha256=None,
        observed_output_byte_length=None,
    )


def _mismatch() -> PublicationRegenerationExportReconciliation:
    return PublicationRegenerationExportReconciliation(
        schema_version=_SCHEMA_VERSION,
        regeneration_id="regen-276-mismatch",
        receipt_sha256=_RECEIPT_DIGEST,
        status="content_mismatch",
        expected_business_output_sha256=_EXPECTED_DIGEST,
        expected_output_byte_length=17,
        observed_business_output_sha256=_OBSERVED_DIGEST,
        observed_output_byte_length=21,
    )


def _assert_fixed_error(error: ValueError, message: str, classification: str) -> None:
    assert str(error) == message
    assert error.detail.classification == classification  # type: ignore[attr-defined]


def _assert_evidence_error(
    error: PublicationRegenerationExportReconciliationEvidenceError,
    classification: str,
) -> None:
    _assert_fixed_error(
        error,
        "publication regeneration export reconciliation evidence is invalid",
        classification,
    )


def _assert_load_error(
    error: PublicationRegenerationExportReconciliationEvidenceLoadError,
    classification: str,
) -> None:
    _assert_fixed_error(
        error,
        "publication regeneration export reconciliation evidence could not be loaded",
        classification,
    )


def _assert_persistence_error(
    error: PublicationRegenerationExportReconciliationEvidencePersistenceError,
    classification: str,
) -> None:
    _assert_fixed_error(
        error,
        "publication regeneration export reconciliation evidence persistence failed",
        classification,
    )


def test_matched_serializes_to_exact_compact_canonical_json(
    matched: PublicationRegenerationExportReconciliation,
) -> None:
    expected = (
        '{"expected_business_output_sha256":"'
        + _EXPECTED_DIGEST
        + '","expected_output_byte_length":17,"observed_business_output_sha256":"'
        + _EXPECTED_DIGEST
        + '","observed_output_byte_length":17,"receipt_sha256":"'
        + _RECEIPT_DIGEST
        + '","regeneration_id":"regen-276-日本語😀",'
        '"schema_version":"publication-regeneration-export-reconciliation.v1",'
        '"status":"matched"}'
    )
    assert serialize_publication_regeneration_export_reconciliation_canonical(
        matched
    ) == expected


def test_missing_serializes_to_exact_canonical_null_fields() -> None:
    missing = _missing()
    value = json.loads(
        serialize_publication_regeneration_export_reconciliation_canonical(missing)
    )
    assert value == {
        "schema_version": _SCHEMA_VERSION,
        "regeneration_id": "regen-276-missing",
        "receipt_sha256": _RECEIPT_DIGEST,
        "status": "missing",
        "expected_business_output_sha256": _EXPECTED_DIGEST,
        "expected_output_byte_length": 17,
        "observed_business_output_sha256": None,
        "observed_output_byte_length": None,
    }
    assert '"observed_business_output_sha256":null' in (
        serialize_publication_regeneration_export_reconciliation_canonical(missing)
    )
    assert '"observed_output_byte_length":null' in (
        serialize_publication_regeneration_export_reconciliation_canonical(missing)
    )


def test_content_mismatch_serializes_exact_observed_identities() -> None:
    mismatch = _mismatch()
    value = json.loads(
        serialize_publication_regeneration_export_reconciliation_canonical(mismatch)
    )
    assert value["status"] == "content_mismatch"
    assert value["expected_business_output_sha256"] == _EXPECTED_DIGEST
    assert value["expected_output_byte_length"] == 17
    assert value["observed_business_output_sha256"] == _OBSERVED_DIGEST
    assert value["observed_output_byte_length"] == 21


@pytest.mark.parametrize("reconciliation", [_matched(), _missing(), _mismatch()])
def test_canonical_json_has_exact_keys_and_exact_values(
    reconciliation: PublicationRegenerationExportReconciliation,
) -> None:
    canonical = serialize_publication_regeneration_export_reconciliation_canonical(
        reconciliation
    )
    assert set(json.loads(canonical)) == _RECONCILIATION_KEYS
    assert json.loads(canonical) == {
        field: getattr(reconciliation, field)
        for field in _RECONCILIATION_KEYS
    }
    assert canonical == canonical.rstrip("\n")
    assert "\ufeff" not in canonical
    assert " " not in canonical


@pytest.mark.parametrize("reconciliation", [_matched(), _missing(), _mismatch()])
def test_canonical_bytes_are_exact_utf8_without_bom_or_newline(
    reconciliation: PublicationRegenerationExportReconciliation,
) -> None:
    canonical = serialize_publication_regeneration_export_reconciliation_canonical(
        reconciliation
    )
    encoded = publication_regeneration_export_reconciliation_canonical_bytes(
        reconciliation
    )
    assert encoded == canonical.encode("utf-8")
    assert not encoded.startswith(b"\xef\xbb\xbf")
    assert not encoded.endswith(b"\n")


@pytest.mark.parametrize("reconciliation", [_matched(), _missing(), _mismatch()])
def test_digest_is_sha256_of_exact_canonical_bytes(
    reconciliation: PublicationRegenerationExportReconciliation,
) -> None:
    canonical = publication_regeneration_export_reconciliation_canonical_bytes(
        reconciliation
    )
    assert publication_regeneration_export_reconciliation_digest(reconciliation) == (
        hashlib.sha256(canonical).hexdigest()
    )


def test_exact_model_type_is_required_for_serialization() -> None:
    values = _matched().__dict__.copy()

    class Child(PublicationRegenerationExportReconciliation):
        pass

    child = object.__new__(Child)
    for name, value in values.items():
        object.__setattr__(child, name, value)

    class Compatible:
        pass

    compatible = Compatible()
    for name, value in values.items():
        setattr(compatible, name, value)

    for candidate in (child, compatible, values):
        with pytest.raises(
            PublicationRegenerationExportReconciliationEvidenceError
        ) as raised:
            serialize_publication_regeneration_export_reconciliation_canonical(
                candidate  # type: ignore[arg-type]
            )
        _assert_evidence_error(raised.value, "reconciliation_type")


def test_forged_impossible_model_is_rejected_before_filesystem_mutation(
    tmp_path: Path,
) -> None:
    forged = object.__new__(PublicationRegenerationExportReconciliation)
    for name, value in _matched().__dict__.items():
        object.__setattr__(forged, name, value)
    object.__setattr__(forged, "status", "matched")
    object.__setattr__(forged, "observed_output_byte_length", 999)
    target = tmp_path / "evidence.json"

    with pytest.raises(
        PublicationRegenerationExportReconciliationEvidenceError
    ) as raised:
        persist_publication_regeneration_export_reconciliation(target, forged)

    _assert_evidence_error(raised.value, "reconciliation")
    assert not target.exists()


def test_new_target_persists_exact_bytes_and_strict_loads(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
) -> None:
    path = tmp_path / "evidence.json"

    persist_publication_regeneration_export_reconciliation(path, matched)

    expected = publication_regeneration_export_reconciliation_canonical_bytes(matched)
    assert path.read_bytes() == expected
    assert load_publication_regeneration_export_reconciliation(path) == matched


def test_new_persistence_orders_write_flush_file_fsync_close_parent_fsync(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "ordered.json"
    original_open = evidence_module.Path.open
    events: list[str] = []

    class TrackingHandle:
        def __init__(self, handle: object) -> None:
            self.handle = handle

        def write(self, contents: bytes) -> int:
            events.append("write")
            return self.handle.write(contents)  # type: ignore[attr-defined]

        def flush(self) -> None:
            events.append("flush")
            self.handle.flush()  # type: ignore[attr-defined]

        def fileno(self) -> int:
            events.append("fileno")
            return self.handle.fileno()  # type: ignore[attr-defined]

        def close(self) -> None:
            events.append("close")
            self.handle.close()  # type: ignore[attr-defined]

    def tracked_open(
        target: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        handle = original_open(target, mode, *args, **kwargs)
        if target == path and mode == "xb":
            return TrackingHandle(handle)
        return handle

    monkeypatch.setattr(evidence_module.Path, "open", tracked_open)
    monkeypatch.setattr(
        evidence_module.os,
        "fsync",
        lambda _descriptor: events.append("file_fsync"),
    )
    monkeypatch.setattr(
        evidence_module,
        "_fsync_evidence_directory",
        lambda _directory: events.append("parent_fsync"),
    )

    persist_publication_regeneration_export_reconciliation(path, matched)

    assert events == ["write", "flush", "fileno", "file_fsync", "close", "parent_fsync"]


def test_identical_target_is_idempotent_without_rewrite_and_fsyncs_both(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "identical.json"
    contents = publication_regeneration_export_reconciliation_canonical_bytes(matched)
    path.write_bytes(contents)
    events: list[str] = []
    original_open = evidence_module.Path.open

    def tracked_open(
        target: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        events.append(mode)
        return original_open(target, mode, *args, **kwargs)

    monkeypatch.setattr(evidence_module.Path, "open", tracked_open)
    monkeypatch.setattr(
        evidence_module.os,
        "fsync",
        lambda _descriptor: events.append("file_fsync"),
    )
    monkeypatch.setattr(
        evidence_module,
        "_fsync_evidence_directory",
        lambda _directory: events.append("parent_fsync"),
    )
    monkeypatch.setattr(
        evidence_module.Path,
        "write_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("rewrite attempted")
        ),
    )

    persist_publication_regeneration_export_reconciliation(path, matched)

    assert events == ["xb", "rb", "rb", "file_fsync", "parent_fsync"]
    assert path.read_bytes() == contents


def test_existing_different_target_conflicts_and_remains_unchanged(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
) -> None:
    path = tmp_path / "conflict.json"
    original = b"different evidence bytes"
    path.write_bytes(original)

    with pytest.raises(
        PublicationRegenerationExportReconciliationEvidenceConflictError
    ) as raised:
        persist_publication_regeneration_export_reconciliation(path, matched)

    _assert_persistence_error(raised.value, "conflict")
    assert path.read_bytes() == original


def test_corrupt_existing_target_is_conflict_and_never_repaired(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
) -> None:
    path = tmp_path / "corrupt.json"
    corrupt = b"not canonical json\n"
    path.write_bytes(corrupt)

    with pytest.raises(
        PublicationRegenerationExportReconciliationEvidenceConflictError
    ):
        persist_publication_regeneration_export_reconciliation(path, matched)

    assert path.read_bytes() == corrupt


def test_missing_parent_rejects_without_parent_creation(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
) -> None:
    parent = tmp_path / "missing-parent"
    path = parent / "evidence.json"

    with pytest.raises(
        PublicationRegenerationExportReconciliationEvidencePersistenceError
    ) as raised:
        persist_publication_regeneration_export_reconciliation(path, matched)

    _assert_persistence_error(raised.value, "parent")
    assert not parent.exists()


@pytest.mark.parametrize("target_kind", ["directory", "symlink", "fifo"])
def test_directory_symlink_and_special_targets_reject_safely(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
    target_kind: str,
) -> None:
    target = tmp_path / target_kind
    if target_kind == "directory":
        target.mkdir()
    elif target_kind == "symlink":
        real = tmp_path / "real.json"
        real.write_bytes(b"real")
        try:
            target.symlink_to(real)
        except OSError:
            pytest.skip("symlinks are unavailable")
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("special files are unavailable")
        try:
            os.mkfifo(target)
        except OSError:
            pytest.skip("FIFO creation is unavailable")

    with pytest.raises(
        PublicationRegenerationExportReconciliationEvidencePersistenceError
    ) as raised:
        persist_publication_regeneration_export_reconciliation(target, matched)

    _assert_persistence_error(raised.value, "target")


def test_wrong_persistence_path_type_rejects_before_mutation(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
) -> None:
    with pytest.raises(
        PublicationRegenerationExportReconciliationEvidencePersistenceError
    ) as raised:
        persist_publication_regeneration_export_reconciliation(
            str(tmp_path / "evidence.json"),  # type: ignore[arg-type]
            matched,
        )

    _assert_persistence_error(raised.value, "path_type")


@pytest.mark.parametrize("race_mode", ["identical", "different"])
def test_race_created_target_has_single_attempt_and_safe_result(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
    monkeypatch: pytest.MonkeyPatch,
    race_mode: str,
) -> None:
    path = tmp_path / f"race-{race_mode}.json"
    contents = publication_regeneration_export_reconciliation_canonical_bytes(matched)
    original_open = evidence_module.Path.open
    injected = False
    modes: list[str] = []
    competing = contents if race_mode == "identical" else b"race-created conflict"

    def race_open(
        target: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        nonlocal injected
        modes.append(mode)
        if target == path and mode == "xb" and not injected:
            injected = True
            with original_open(target, "wb") as handle:
                handle.write(competing)
            raise FileExistsError
        return original_open(target, mode, *args, **kwargs)

    monkeypatch.setattr(evidence_module.Path, "open", race_open)

    if race_mode == "identical":
        persist_publication_regeneration_export_reconciliation(path, matched)
    else:
        with pytest.raises(
            PublicationRegenerationExportReconciliationEvidenceConflictError
        ):
            persist_publication_regeneration_export_reconciliation(path, matched)

    assert modes.count("xb") == 1
    assert "wb" not in modes
    assert path.read_bytes() == competing


@pytest.mark.parametrize(
    "failure", ["write", "flush", "file_fsync", "close", "parent_fsync"]
)
def test_post_create_failures_are_ambiguous_retain_artifact_and_do_not_retry(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    path = tmp_path / f"{failure}.json"
    original_open = evidence_module.Path.open
    open_count = 0

    class FailureHandle:
        def __init__(self, handle: object) -> None:
            self.handle = handle

        def write(self, contents: bytes) -> int:
            if failure == "write":
                return 0
            return self.handle.write(contents)  # type: ignore[attr-defined]

        def flush(self) -> None:
            if failure == "flush":
                raise OSError("flush failure")
            self.handle.flush()  # type: ignore[attr-defined]

        def fileno(self) -> int:
            return self.handle.fileno()  # type: ignore[attr-defined]

        def close(self) -> None:
            if failure == "close":
                raise OSError("close failure")
            self.handle.close()  # type: ignore[attr-defined]

    def failure_open(
        target: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        nonlocal open_count
        if target == path and mode == "xb":
            open_count += 1
            return FailureHandle(original_open(target, mode, *args, **kwargs))
        return original_open(target, mode, *args, **kwargs)

    monkeypatch.setattr(evidence_module.Path, "open", failure_open)
    if failure == "file_fsync":
        monkeypatch.setattr(
            evidence_module.os,
            "fsync",
            lambda _descriptor: (_ for _ in ()).throw(OSError("fsync failure")),
        )
    if failure == "parent_fsync":
        monkeypatch.setattr(
            evidence_module,
            "_fsync_evidence_directory",
            lambda _directory: (_ for _ in ()).throw(
                OSError("parent fsync failure")
            ),
        )

    with pytest.raises(
        PublicationRegenerationExportReconciliationEvidencePersistenceError
    ) as raised:
        persist_publication_regeneration_export_reconciliation(path, matched)

    _assert_persistence_error(raised.value, "ambiguous")
    assert open_count == 1
    assert path.exists()
    if failure != "write":
        assert path.read_bytes() == (
            publication_regeneration_export_reconciliation_canonical_bytes(matched)
        )


def test_round_trip_loads_matched_missing_and_mismatch_without_reinterpretation(
    tmp_path: Path,
) -> None:
    for name, reconciliation in (
        ("matched", _matched()),
        ("missing", _missing()),
        ("mismatch", _mismatch()),
    ):
        path = tmp_path / f"{name}.json"
        persist_publication_regeneration_export_reconciliation(path, reconciliation)
        loaded = load_publication_regeneration_export_reconciliation(path)
        assert type(loaded) is PublicationRegenerationExportReconciliation
        assert loaded == reconciliation
        assert loaded.status == reconciliation.status
        assert loaded.observed_business_output_sha256 == (
            reconciliation.observed_business_output_sha256
        )
        assert loaded.observed_output_byte_length == (
            reconciliation.observed_output_byte_length
        )


def test_load_requires_exact_path_type_and_rejects_missing_target(
    tmp_path: Path,
) -> None:
    for candidate in (
        str(tmp_path / "evidence.json"),
        tmp_path / "missing.json",
    ):
        with pytest.raises(
            PublicationRegenerationExportReconciliationEvidenceLoadError
        ) as raised:
            load_publication_regeneration_export_reconciliation(candidate)  # type: ignore[arg-type]
        classification = "path_type" if isinstance(candidate, str) else "target"
        _assert_load_error(raised.value, classification)


@pytest.mark.parametrize(
    "bad_kind",
    [
        "duplicate",
        "missing",
        "extra",
        "trailing",
        "invalid_utf8",
        "malformed",
        "constant",
        "pretty",
        "wrong_order",
    ],
)
def test_strict_loader_rejects_noncanonical_and_malformed_forms(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
    bad_kind: str,
) -> None:
    canonical = publication_regeneration_export_reconciliation_canonical_bytes(matched)
    if bad_kind == "duplicate":
        contents = canonical.replace(
            b'"status":"matched"',
            b'"status":"matched","status":"matched"',
            1,
        )
    elif bad_kind == "missing":
        value = json.loads(canonical)
        del value["receipt_sha256"]
        contents = json.dumps(value, separators=(",", ":")).encode()
    elif bad_kind == "extra":
        contents = canonical.replace(b"{", b'{"extra":1,', 1)
    elif bad_kind == "trailing":
        contents = canonical + b"\n"
    elif bad_kind == "invalid_utf8":
        contents = b"\xff"
    elif bad_kind == "malformed":
        contents = b'{"schema_version"'
    elif bad_kind == "constant":
        contents = canonical.replace(
            b'"expected_output_byte_length":17',
            b'"expected_output_byte_length":NaN',
            1,
        )
    elif bad_kind == "pretty":
        contents = json.dumps(
            json.loads(canonical), indent=2, ensure_ascii=False
        ).encode()
    else:
        value = json.loads(canonical)
        contents = json.dumps(
            {"status": value.pop("status"), **value},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()

    path = tmp_path / f"{bad_kind}.json"
    path.write_bytes(contents)
    with pytest.raises(
        PublicationRegenerationExportReconciliationEvidenceLoadError
    ) as raised:
        load_publication_regeneration_export_reconciliation(path)

    assert str(raised.value) == (
        "publication regeneration export reconciliation evidence could not be loaded"
    )
    assert path.read_bytes() == contents


@pytest.mark.parametrize(
    "field, replacement",
    [
        ("schema_version", "wrong"),
        ("regeneration_id", ""),
        ("receipt_sha256", "A" * 64),
        ("expected_business_output_sha256", "not-a-digest"),
        ("expected_output_byte_length", True),
        ("expected_output_byte_length", -1),
        ("observed_business_output_sha256", "G" * 64),
        ("observed_output_byte_length", False),
        ("observed_output_byte_length", -1),
        ("status", "unknown"),
    ],
)
def test_loader_rejects_invalid_model_values_without_leaking_details(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
    field: str,
    replacement: object,
) -> None:
    value = json.loads(
        publication_regeneration_export_reconciliation_canonical_bytes(matched)
    )
    value[field] = replacement
    contents = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    path = tmp_path / f"invalid-{field}.json"
    path.write_bytes(contents)

    with pytest.raises(
        PublicationRegenerationExportReconciliationEvidenceLoadError
    ) as raised:
        load_publication_regeneration_export_reconciliation(path)

    _assert_load_error(raised.value, "reconciliation")
    assert str(path) not in str(raised.value)
    assert _EXPECTED_DIGEST not in str(raised.value)
    assert "provider-secret-text" not in str(raised.value)


@pytest.mark.parametrize(
    "status, observed_digest, observed_length",
    [
        ("missing", _EXPECTED_DIGEST, None),
        ("missing", None, 17),
        ("matched", _OBSERVED_DIGEST, 17),
        ("matched", _EXPECTED_DIGEST, 99),
        ("content_mismatch", _EXPECTED_DIGEST, 17),
    ],
)
def test_loader_rejects_impossible_status_field_combinations(
    tmp_path: Path,
    status: str,
    observed_digest: str | None,
    observed_length: int | None,
) -> None:
    value = {
        "schema_version": _SCHEMA_VERSION,
        "regeneration_id": "regen-invalid-status",
        "receipt_sha256": _RECEIPT_DIGEST,
        "status": status,
        "expected_business_output_sha256": _EXPECTED_DIGEST,
        "expected_output_byte_length": 17,
        "observed_business_output_sha256": observed_digest,
        "observed_output_byte_length": observed_length,
    }
    path = tmp_path / f"invalid-{status}-{observed_length}.json"
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(
        PublicationRegenerationExportReconciliationEvidenceLoadError
    ) as raised:
        load_publication_regeneration_export_reconciliation(path)

    _assert_load_error(raised.value, "reconciliation")


def test_loader_rejects_directory_symlink_and_special_target(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(
        PublicationRegenerationExportReconciliationEvidenceLoadError
    ) as raised:
        load_publication_regeneration_export_reconciliation(directory)
    _assert_load_error(raised.value, "target")

    symlink = tmp_path / "symlink"
    try:
        symlink.symlink_to(directory, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(
        PublicationRegenerationExportReconciliationEvidenceLoadError
    ) as raised:
        load_publication_regeneration_export_reconciliation(symlink)
    _assert_load_error(raised.value, "target")

    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "fifo"
        try:
            os.mkfifo(fifo)
        except OSError:
            pytest.skip("FIFO creation is unavailable")
        with pytest.raises(
            PublicationRegenerationExportReconciliationEvidenceLoadError
        ) as raised:
            load_publication_regeneration_export_reconciliation(fifo)
        _assert_load_error(raised.value, "target")

    assert matched.status == "matched"


def test_error_strings_are_fixed_and_do_not_leak_path_bytes_or_digests(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
) -> None:
    secret_path = tmp_path / "secret-evidence-path.json"
    secret_bytes = b"secret evidence bytes"
    secret_path.write_bytes(secret_bytes)
    with pytest.raises(
        PublicationRegenerationExportReconciliationEvidenceConflictError
    ) as raised:
        persist_publication_regeneration_export_reconciliation(secret_path, matched)

    message = str(raised.value)
    assert message == (
        "publication regeneration export reconciliation evidence persistence failed"
    )
    assert str(secret_path) not in message
    assert secret_bytes.decode() not in message
    assert _EXPECTED_DIGEST not in message
    assert _RECEIPT_DIGEST not in message


def test_persistence_and_load_never_call_phase275_or_phase274_or_read_output(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_path = tmp_path / "business-output.txt"
    output_path.write_bytes(b"private business output")
    evidence_path = tmp_path / "evidence.json"
    forbidden_calls: list[str] = []

    def forbidden(name: str):
        def fail(*_args: object, **_kwargs: object) -> object:
            forbidden_calls.append(name)
            raise AssertionError(f"forbidden call: {name}")

        return fail

    import ai_office.engine.publication_regeneration_export_receipt as phase274
    import ai_office.engine.publication_regeneration_export_reconciliation as phase275

    monkeypatch.setattr(
        phase275,
        "reconcile_publication_regeneration_export",
        forbidden("phase275"),
    )
    monkeypatch.setattr(
        phase274,
        "load_publication_regeneration_export_receipt",
        forbidden("phase274-load"),
    )
    monkeypatch.setattr(
        phase274,
        "publication_regeneration_export_receipt_digest",
        forbidden("phase274-digest"),
    )
    real_read_bytes = evidence_module.Path.read_bytes

    def guarded_read(path: Path) -> bytes:
        if path == output_path:
            raise AssertionError("output file was read")
        return real_read_bytes(path)

    monkeypatch.setattr(evidence_module.Path, "read_bytes", guarded_read)

    persist_publication_regeneration_export_reconciliation(evidence_path, matched)
    assert load_publication_regeneration_export_reconciliation(evidence_path) == matched
    assert forbidden_calls == []
    assert real_read_bytes(output_path) == b"private business output"


def test_lineage_artifacts_and_output_remain_unchanged(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
) -> None:
    lineage = tmp_path / "lineage"
    lineage.mkdir()
    artifacts = {
        "receipt.json": b"receipt",
        "business-output.txt": b"output",
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
    before = {name: (lineage / name).read_bytes() for name in artifacts}
    evidence_path = lineage / "reconciliation-evidence.json"

    persist_publication_regeneration_export_reconciliation(evidence_path, matched)
    assert {name: (lineage / name).read_bytes() for name in artifacts} == before
    assert load_publication_regeneration_export_reconciliation(evidence_path) == matched
    assert {name: (lineage / name).read_bytes() for name in artifacts} == before


def test_no_provider_network_environment_or_clock_access(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden external access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(evidence_module.os, "getenv", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    path = tmp_path / "evidence.json"

    persist_publication_regeneration_export_reconciliation(path, matched)
    assert load_publication_regeneration_export_reconciliation(path) == matched


def test_only_explicit_evidence_path_is_created(
    tmp_path: Path,
    matched: PublicationRegenerationExportReconciliation,
) -> None:
    before = set(tmp_path.iterdir())
    path = tmp_path / "explicit-evidence.json"
    persist_publication_regeneration_export_reconciliation(path, matched)
    after = set(tmp_path.iterdir())
    assert after - before == {path}


def test_public_engine_exports_are_available() -> None:
    import ai_office.engine as engine

    for name in (
        "PublicationRegenerationExportReconciliationEvidenceError",
        "PublicationRegenerationExportReconciliationEvidenceFailureDetail",
        "PublicationRegenerationExportReconciliationEvidencePersistenceError",
        "PublicationRegenerationExportReconciliationEvidenceConflictError",
        "PublicationRegenerationExportReconciliationEvidenceLoadError",
        "serialize_publication_regeneration_export_reconciliation_canonical",
        "publication_regeneration_export_reconciliation_canonical_bytes",
        "publication_regeneration_export_reconciliation_digest",
        "persist_publication_regeneration_export_reconciliation",
        "load_publication_regeneration_export_reconciliation",
    ):
        assert hasattr(engine, name)


def test_evidence_module_has_no_observation_or_receipt_loader_dependency() -> None:
    source = Path(evidence_module.__file__).read_text(encoding="utf-8")
    for forbidden_name in (
        "reconcile_publication_regeneration_export(",
        "load_publication_regeneration_export_receipt(",
        "publication_regeneration_export_receipt_digest(",
        "export_publication_regeneration_output(",
        "project_publication_regeneration_output(",
    ):
        assert forbidden_name not in source
