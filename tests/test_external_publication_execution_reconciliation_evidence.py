"""Focused provider-free tests for Phase 284 reconciliation evidence."""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import os
import random
import secrets
import socket
import time
import uuid
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_office.engine.external_publication as claim_module
import ai_office.engine.external_publication_execution as execution_module
from ai_office.engine import (
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationExecutionReconciliationEvidenceConflictError,
    ExternalPublicationExecutionReconciliationEvidenceError,
    ExternalPublicationExecutionReconciliationEvidenceFailureDetail,
    ExternalPublicationExecutionReconciliationEvidenceLoadError,
    ExternalPublicationExecutionReconciliationEvidencePersistenceError,
    external_publication_execution_reconciliation_canonical_bytes,
    external_publication_execution_reconciliation_digest,
    load_external_publication_execution_reconciliation,
    persist_external_publication_execution_reconciliation,
    serialize_external_publication_execution_reconciliation_canonical,
)
from ai_office.engine import (
    external_publication_execution_evidence as execution_evidence_module,
)
from ai_office.engine import (
    external_publication_execution_reconciliation as reconciliation_module,
)
from ai_office.engine import (
    external_publication_execution_reconciliation_evidence as evidence_module,
)

_SCHEMA = "external-publication-execution-reconciliation.v1"
_CLAIM_DIGEST = "a" * 64
_EXECUTION_DIGEST = "b" * 64
_FIELDS = (
    "publication_attempt_claim_sha256",
    "regeneration_id",
    "publication_plan_sha256",
    "publication_approval_sha256",
    "business_output_sha256",
    "output_byte_length",
    "provider",
    "publication_target_sha256",
)


def _matched() -> ExternalPublicationExecutionReconciliation:
    return ExternalPublicationExecutionReconciliation(
        schema_version=_SCHEMA,
        claim_sha256=_CLAIM_DIGEST,
        execution_evidence_sha256=_EXECUTION_DIGEST,
        status="matched",
        mismatched_fields=(),
    )


def _mismatch() -> ExternalPublicationExecutionReconciliation:
    return ExternalPublicationExecutionReconciliation(
        schema_version=_SCHEMA,
        claim_sha256=_CLAIM_DIGEST,
        execution_evidence_sha256=_EXECUTION_DIGEST,
        status="lineage_mismatch",
        mismatched_fields=_FIELDS,
    )


def _canonical_mapping(
    reconciliation: ExternalPublicationExecutionReconciliation,
) -> dict[str, object]:
    return {
        "claim_sha256": reconciliation.claim_sha256,
        "execution_evidence_sha256": reconciliation.execution_evidence_sha256,
        "mismatched_fields": list(reconciliation.mismatched_fields),
        "schema_version": reconciliation.schema_version,
        "status": reconciliation.status,
    }


def _canonical_bytes(
    reconciliation: ExternalPublicationExecutionReconciliation,
) -> bytes:
    return json.dumps(
        _canonical_mapping(reconciliation),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _forged_instance(cls: type[object], source: object) -> object:
    value = object.__new__(cls)
    for field in dataclasses.fields(source):  # type: ignore[arg-type]
        object.__setattr__(value, field.name, getattr(source, field.name))
    return value


def _assert_evidence_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationExecutionReconciliationEvidenceError
    assert str(error) == (
        "external publication execution reconciliation evidence is invalid"
    )
    assert (
        type(error.detail)
        is ExternalPublicationExecutionReconciliationEvidenceFailureDetail
    )
    assert error.detail.classification == classification


def _assert_persistence_error(error: ValueError, classification: str) -> None:
    assert (
        type(error)
        is ExternalPublicationExecutionReconciliationEvidencePersistenceError
    )
    assert str(error) == (
        "external publication execution reconciliation evidence persistence failed"
    )
    assert (
        type(error.detail)
        is ExternalPublicationExecutionReconciliationEvidenceFailureDetail
    )
    assert error.detail.classification == classification


def _assert_conflict_error(error: ValueError) -> None:
    assert (
        type(error) is ExternalPublicationExecutionReconciliationEvidenceConflictError
    )
    assert str(error) == (
        "external publication execution reconciliation evidence persistence failed"
    )
    assert (
        type(error.detail)
        is ExternalPublicationExecutionReconciliationEvidenceFailureDetail
    )
    assert error.detail.classification == "conflict"


def _assert_load_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationExecutionReconciliationEvidenceLoadError
    assert str(error) == (
        "external publication execution reconciliation evidence could not be loaded"
    )
    assert (
        type(error.detail)
        is ExternalPublicationExecutionReconciliationEvidenceFailureDetail
    )
    assert error.detail.classification == classification


def test_reconciliation_model_is_exact_frozen_five_field_contract() -> None:
    result = _matched()

    assert type(result) is ExternalPublicationExecutionReconciliation
    assert tuple(field.name for field in fields(result)) == (
        "schema_version",
        "claim_sha256",
        "execution_evidence_sha256",
        "status",
        "mismatched_fields",
    )
    assert result.__dataclass_params__.frozen is True
    with pytest.raises(FrozenInstanceError):
        result.status = "lineage_mismatch"  # type: ignore[misc]


def test_matched_canonical_json_has_exact_five_keys_and_no_wrapper() -> None:
    result = _matched()
    expected = (
        '{"claim_sha256":"'
        + _CLAIM_DIGEST
        + '","execution_evidence_sha256":"'
        + _EXECUTION_DIGEST
        + '","mismatched_fields":[],"schema_version":"'
        + _SCHEMA
        + '","status":"matched"}'
    )

    canonical = serialize_external_publication_execution_reconciliation_canonical(
        result
    )

    assert canonical == expected
    assert json.loads(canonical) == _canonical_mapping(result)
    assert set(json.loads(canonical)) == {
        "schema_version",
        "claim_sha256",
        "execution_evidence_sha256",
        "status",
        "mismatched_fields",
    }
    assert len(json.loads(canonical)) == 5
    assert "evidence_id" not in canonical
    assert "timestamp" not in canonical
    assert "path" not in canonical
    assert "publication_id" not in canonical
    assert "approval" not in canonical
    assert "provider" not in canonical
    assert "\\u" not in canonical
    assert " " not in canonical
    assert "\n" not in canonical


def test_lineage_mismatch_serializes_ordered_tuple_as_json_array() -> None:
    result = _mismatch()
    canonical = serialize_external_publication_execution_reconciliation_canonical(
        result
    )

    assert json.loads(canonical) == _canonical_mapping(result)
    assert json.loads(canonical)["mismatched_fields"] == list(_FIELDS)
    assert canonical.index('"publication_attempt_claim_sha256"') < canonical.index(
        '"regeneration_id"'
    )
    assert canonical.endswith('"status":"lineage_mismatch"}')


def test_canonical_bytes_are_exact_utf8_without_bom_or_trailing_newline() -> None:
    for result in (_matched(), _mismatch()):
        canonical = serialize_external_publication_execution_reconciliation_canonical(
            result
        )
        encoded = external_publication_execution_reconciliation_canonical_bytes(result)

        assert encoded == canonical.encode("utf-8") == _canonical_bytes(result)
        assert not encoded.startswith(b"\xef\xbb\xbf")
        assert not encoded.endswith(b"\n")


def test_digest_is_lowercase_sha256_of_exact_canonical_bytes() -> None:
    for result in (_matched(), _mismatch()):
        canonical = external_publication_execution_reconciliation_canonical_bytes(
            result
        )
        digest = external_publication_execution_reconciliation_digest(result)

        assert digest == hashlib.sha256(canonical).hexdigest()
        assert len(digest) == 64
        assert digest == digest.lower()


@pytest.mark.parametrize("operation", ["serialize", "bytes", "digest", "persist"])
def test_public_boundaries_require_exact_reconciliation_type(
    tmp_path: Path,
    operation: str,
) -> None:
    result = _matched()

    class ReconciliationChild(ExternalPublicationExecutionReconciliation):
        pass

    candidates = (
        _forged_instance(ReconciliationChild, result),
        SimpleNamespace(**result.__dict__),
        result.__dict__.copy(),
    )
    for index, candidate in enumerate(candidates):
        path = tmp_path / f"candidate-{index}.json"
        if operation == "serialize":
            with pytest.raises(
                ExternalPublicationExecutionReconciliationEvidenceError
            ) as raised:
                serialize_external_publication_execution_reconciliation_canonical(
                    candidate  # type: ignore[arg-type]
                )
            _assert_evidence_error(raised.value, "reconciliation_type")
        elif operation == "bytes":
            with pytest.raises(
                ExternalPublicationExecutionReconciliationEvidenceError
            ) as raised:
                external_publication_execution_reconciliation_canonical_bytes(
                    candidate  # type: ignore[arg-type]
                )
            _assert_evidence_error(raised.value, "reconciliation_type")
        elif operation == "digest":
            with pytest.raises(
                ExternalPublicationExecutionReconciliationEvidenceError
            ) as raised:
                external_publication_execution_reconciliation_digest(
                    candidate  # type: ignore[arg-type]
                )
            _assert_evidence_error(raised.value, "reconciliation_type")
        else:
            with pytest.raises(
                ExternalPublicationExecutionReconciliationEvidenceError
            ) as raised:
                persist_external_publication_execution_reconciliation(
                    path,
                    candidate,  # type: ignore[arg-type]
                )
            _assert_evidence_error(raised.value, "reconciliation_type")
            assert not path.exists()


def test_forged_exact_instance_is_reconstructed_and_rejected_before_mutation(
    tmp_path: Path,
) -> None:
    forged = _forged_instance(
        ExternalPublicationExecutionReconciliation,
        _matched(),
    )
    object.__setattr__(forged, "status", "lineage_mismatch")
    object.__setattr__(forged, "mismatched_fields", ())
    path = tmp_path / "forged.json"

    with pytest.raises(
        ExternalPublicationExecutionReconciliationEvidenceError
    ) as raised:
        persist_external_publication_execution_reconciliation(path, forged)  # type: ignore[arg-type]

    _assert_evidence_error(raised.value, "reconciliation")
    assert not path.exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": "wrong"},
        {"claim_sha256": "A" * 64},
        {"execution_evidence_sha256": "b" * 63},
        {"status": "lineage_mismatch", "mismatched_fields": ()},
        {"status": "matched", "mismatched_fields": ("provider",)},
        {"mismatched_fields": ["provider"]},
        {"mismatched_fields": ("provider", "provider")},
        {"mismatched_fields": ("provider", "publication_plan_sha256")},
        {"mismatched_fields": ("unknown",)},
    ],
)
def test_forged_exact_model_invariants_fail_closed(
    overrides: dict[str, object],
) -> None:
    forged = _forged_instance(
        ExternalPublicationExecutionReconciliation,
        _matched(),
    )
    for field, value in overrides.items():
        object.__setattr__(forged, field, value)

    with pytest.raises(
        ExternalPublicationExecutionReconciliationEvidenceError
    ) as raised:
        serialize_external_publication_execution_reconciliation_canonical(
            forged  # type: ignore[arg-type]
        )

    _assert_evidence_error(raised.value, "reconciliation")


def test_persistence_validates_and_derives_bytes_before_filesystem_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forged = _forged_instance(
        ExternalPublicationExecutionReconciliation,
        _matched(),
    )
    object.__setattr__(forged, "mismatched_fields", ["provider"])
    path = tmp_path / "not-created.json"
    calls: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> object:
        calls.append("filesystem")
        raise AssertionError("filesystem mutation happened before validation")

    monkeypatch.setattr(evidence_module.Path, "open", forbidden)
    monkeypatch.setattr(evidence_module.os, "open", forbidden)
    monkeypatch.setattr(evidence_module.Path, "unlink", forbidden)

    with pytest.raises(
        ExternalPublicationExecutionReconciliationEvidenceError
    ) as raised:
        persist_external_publication_execution_reconciliation(path, forged)  # type: ignore[arg-type]

    _assert_evidence_error(raised.value, "reconciliation")
    assert calls == []
    assert not path.exists()


def test_persistence_requires_existing_parent_and_exact_concrete_path(
    tmp_path: Path,
) -> None:
    result = _matched()
    missing_parent = tmp_path / "missing-parent" / "evidence.json"

    with pytest.raises(
        ExternalPublicationExecutionReconciliationEvidencePersistenceError
    ) as raised:
        persist_external_publication_execution_reconciliation(missing_parent, result)
    _assert_persistence_error(raised.value, "parent")
    assert not missing_parent.parent.exists()

    class PathChild(type(Path())):
        pass

    with pytest.raises(
        ExternalPublicationExecutionReconciliationEvidencePersistenceError
    ) as raised:
        persist_external_publication_execution_reconciliation(
            PathChild(tmp_path / "child.json"),
            result,
        )
    _assert_persistence_error(raised.value, "path_type")
    assert not (tmp_path / "child.json").exists()


@pytest.mark.parametrize("target_kind", ["symlink", "directory", "fifo"])
def test_persistence_rejects_symlink_directory_and_nonregular_targets(
    tmp_path: Path,
    target_kind: str,
) -> None:
    target = tmp_path / target_kind
    if target_kind == "symlink":
        real = tmp_path / "real.json"
        real.write_bytes(b"real")
        try:
            target.symlink_to(real)
        except OSError:
            pytest.skip("symlinks are unavailable")
    elif target_kind == "directory":
        target.mkdir()
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("special files are unavailable")
        try:
            os.mkfifo(target)
        except OSError:
            pytest.skip("FIFO creation is unavailable")

    with pytest.raises(
        ExternalPublicationExecutionReconciliationEvidencePersistenceError
    ) as raised:
        persist_external_publication_execution_reconciliation(target, _matched())
    _assert_persistence_error(raised.value, "target")


def test_new_target_uses_exclusive_create_and_exact_canonical_bytes(
    tmp_path: Path,
) -> None:
    result = _mismatch()
    path = tmp_path / "reconciliation.json"

    assert persist_external_publication_execution_reconciliation(path, result) is None
    assert (
        path.read_bytes()
        == external_publication_execution_reconciliation_canonical_bytes(result)
    )
    assert load_external_publication_execution_reconciliation(path) == result


def test_new_persistence_orders_write_flush_file_fsync_close_directory_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _matched()
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
        lambda _descriptor: events.append(
            "file_fsync" if "file_fsync" not in events else "directory_fsync"
        ),
    )
    monkeypatch.setattr(
        evidence_module,
        "_fsync_evidence_directory",
        lambda _directory: events.append("directory_fsync"),
    )

    persist_external_publication_execution_reconciliation(path, result)

    assert events == [
        "write",
        "flush",
        "fileno",
        "file_fsync",
        "close",
        "directory_fsync",
    ]
    assert (
        path.read_bytes()
        == external_publication_execution_reconciliation_canonical_bytes(result)
    )


@pytest.mark.parametrize(
    "failure", ["short_write", "write", "flush", "file_fsync", "close"]
)
def test_post_create_file_failures_are_ambiguous_and_retain_artifact_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    result = _matched()
    expected = external_publication_execution_reconciliation_canonical_bytes(result)
    path = tmp_path / f"{failure}.json"
    original_open = evidence_module.Path.open
    open_count = 0
    side_effects: list[str] = []

    class FailureHandle:
        def __init__(self, handle: object) -> None:
            self.handle = handle

        def write(self, contents: bytes) -> int:
            if failure == "short_write":
                self.handle.write(contents[:1])  # type: ignore[attr-defined]
                return 1
            if failure == "write":
                self.handle.write(contents[:1])  # type: ignore[attr-defined]
                raise OSError("write failure")
            return self.handle.write(contents)  # type: ignore[attr-defined]

        def flush(self) -> None:
            if failure == "flush":
                raise OSError("flush failure")
            self.handle.flush()  # type: ignore[attr-defined]

        def fileno(self) -> int:
            return self.handle.fileno()  # type: ignore[attr-defined]

        def close(self) -> None:
            side_effects.append("close")
            self.handle.close()  # type: ignore[attr-defined]
            if failure == "close":
                raise OSError("close failure")

    def tracked_open(
        target: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        nonlocal open_count
        if target == path and mode == "xb":
            open_count += 1
            return FailureHandle(original_open(target, mode, *args, **kwargs))
        return original_open(target, mode, *args, **kwargs)

    monkeypatch.setattr(evidence_module.Path, "open", tracked_open)
    if failure == "file_fsync":
        monkeypatch.setattr(
            evidence_module.os,
            "fsync",
            lambda _descriptor: (_ for _ in ()).throw(OSError("file fsync failure")),
        )

    with pytest.raises(
        ExternalPublicationExecutionReconciliationEvidencePersistenceError
    ) as raised:
        persist_external_publication_execution_reconciliation(path, result)

    _assert_persistence_error(raised.value, "ambiguous")
    assert open_count == 1
    assert path.exists()
    assert "unlink" not in side_effects
    if failure in {"flush", "file_fsync", "close"}:
        assert path.read_bytes() == expected
    elif failure == "short_write":
        assert path.read_bytes() == expected[:1]
    else:
        assert path.read_bytes() == expected[:1]

    # A caller may explicitly resolve ambiguity, but this boundary never retries.
    assert open_count == 1


@pytest.mark.parametrize(
    "failure", ["directory_open", "directory_fsync", "directory_close"]
)
def test_post_create_directory_failures_are_ambiguous_and_retain_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    result = _matched()
    expected = external_publication_execution_reconciliation_canonical_bytes(result)
    path = tmp_path / f"{failure}.json"
    original_os_open = evidence_module.os.open
    original_os_fsync = evidence_module.os.fsync
    original_os_close = evidence_module.os.close
    fsync_calls = 0
    close_calls = 0

    def failing_open(*args: object, **kwargs: object) -> int:
        if failure == "directory_open":
            raise OSError("directory open failure")
        return original_os_open(*args, **kwargs)

    def failing_fsync(descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if failure == "directory_fsync" and fsync_calls == 2:
            raise OSError("directory fsync failure")
        original_os_fsync(descriptor)

    def failing_close(descriptor: int) -> None:
        nonlocal close_calls
        close_calls += 1
        if failure == "directory_close":
            raise OSError("directory close failure")
        original_os_close(descriptor)

    monkeypatch.setattr(evidence_module.os, "open", failing_open)
    monkeypatch.setattr(evidence_module.os, "fsync", failing_fsync)
    monkeypatch.setattr(evidence_module.os, "close", failing_close)

    with pytest.raises(
        ExternalPublicationExecutionReconciliationEvidencePersistenceError
    ) as raised:
        persist_external_publication_execution_reconciliation(path, result)

    _assert_persistence_error(raised.value, "ambiguous")
    assert path.exists()
    assert path.read_bytes() == expected
    if failure == "directory_open":
        assert fsync_calls == 1
    if failure == "directory_close":
        assert close_calls == 1


def test_ambiguous_persistence_does_not_delete_truncate_rewrite_or_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _matched()
    path = tmp_path / "ambiguous.json"
    original_open = evidence_module.Path.open
    calls: list[str] = []

    def failing_open(
        target: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        if target == path and mode == "xb":
            calls.append("xb")
            return _FailingWriteHandle(original_open(target, mode, *args, **kwargs))
        return original_open(target, mode, *args, **kwargs)

    class _FailingWriteHandle:
        def __init__(self, handle: object) -> None:
            self.handle = handle

        def write(self, contents: bytes) -> int:
            calls.append("write")
            self.handle.write(contents[:1])  # type: ignore[attr-defined]
            raise OSError("ambiguous write")

        def close(self) -> None:
            calls.append("close")
            self.handle.close()  # type: ignore[attr-defined]

    monkeypatch.setattr(evidence_module.Path, "open", failing_open)
    monkeypatch.setattr(
        evidence_module.Path,
        "unlink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("delete attempted")
        ),
    )
    monkeypatch.setattr(
        evidence_module.Path,
        "write_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("rewrite attempted")
        ),
    )

    with pytest.raises(
        ExternalPublicationExecutionReconciliationEvidencePersistenceError
    ) as raised:
        persist_external_publication_execution_reconciliation(path, result)

    _assert_persistence_error(raised.value, "ambiguous")
    assert calls == ["xb", "write", "close"]
    assert (
        path.read_bytes()
        == external_publication_execution_reconciliation_canonical_bytes(result)[:1]
    )


@pytest.mark.parametrize("race_mode", ["identical", "different"])
def test_exclusive_create_race_reads_competing_target_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race_mode: str,
) -> None:
    result = _matched()
    canonical = external_publication_execution_reconciliation_canonical_bytes(result)
    competing = canonical if race_mode == "identical" else b"race-created conflict"
    path = tmp_path / f"race-{race_mode}.json"
    original_open = evidence_module.Path.open
    injected = False
    reads = 0

    def race_open(
        target: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        nonlocal injected
        if target == path and mode == "xb" and not injected:
            injected = True
            with original_open(target, "wb") as handle:
                handle.write(competing)
            raise FileExistsError
        return original_open(target, mode, *args, **kwargs)

    original_read_bytes = evidence_module.Path.read_bytes

    def tracked_read_bytes(target: Path) -> bytes:
        nonlocal reads
        if target == path:
            reads += 1
        return original_read_bytes(target)

    monkeypatch.setattr(evidence_module.Path, "open", race_open)
    monkeypatch.setattr(evidence_module.Path, "read_bytes", tracked_read_bytes)

    if race_mode == "identical":
        persist_external_publication_execution_reconciliation(path, result)
    else:
        with pytest.raises(
            ExternalPublicationExecutionReconciliationEvidenceConflictError
        ) as raised:
            persist_external_publication_execution_reconciliation(path, result)
        _assert_conflict_error(raised.value)

    assert reads == 1
    assert path.read_bytes() == competing


def test_identical_existing_canonical_bytes_are_idempotent_without_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _matched()
    canonical = external_publication_execution_reconciliation_canonical_bytes(result)
    path = tmp_path / "identical.json"
    path.write_bytes(canonical)
    before_stat = path.stat()
    events: list[str] = []
    original_open = evidence_module.Path.open
    original_read_bytes = evidence_module.Path.read_bytes

    def tracked_open(
        target: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        events.append(mode)
        return original_open(target, mode, *args, **kwargs)

    def tracked_read_bytes(target: Path) -> bytes:
        events.append("read_bytes")
        return original_read_bytes(target)

    monkeypatch.setattr(evidence_module.Path, "open", tracked_open)
    monkeypatch.setattr(evidence_module.Path, "read_bytes", tracked_read_bytes)
    monkeypatch.setattr(
        evidence_module.Path,
        "write_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("rewrite attempted")
        ),
    )
    monkeypatch.setattr(
        evidence_module.os,
        "fsync",
        lambda _descriptor: events.append("file_fsync"),
    )
    monkeypatch.setattr(
        evidence_module,
        "_fsync_evidence_directory",
        lambda _directory: events.append("directory_fsync"),
    )

    assert persist_external_publication_execution_reconciliation(path, result) is None

    assert events[0] == "xb"
    assert "read_bytes" in events
    assert "rb" in events
    assert events[-2:] == ["file_fsync", "directory_fsync"]
    assert path.read_bytes() == canonical
    assert path.stat().st_mtime_ns == before_stat.st_mtime_ns


@pytest.mark.parametrize(
    "existing",
    [
        b"different evidence bytes",
        b" "
        + external_publication_execution_reconciliation_canonical_bytes(_matched()),
    ],
)
def test_existing_different_or_noncanonical_bytes_conflict_unchanged(
    tmp_path: Path,
    existing: bytes,
) -> None:
    path = tmp_path / f"conflict-{len(existing)}.json"
    path.write_bytes(existing)

    with pytest.raises(
        ExternalPublicationExecutionReconciliationEvidenceConflictError
    ) as raised:
        persist_external_publication_execution_reconciliation(path, _matched())

    _assert_conflict_error(raised.value)
    assert path.read_bytes() == existing


@pytest.mark.parametrize("target_kind", ["missing", "symlink", "directory", "fifo"])
def test_loader_rejects_invalid_targets_without_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
) -> None:
    target = tmp_path / target_kind
    if target_kind == "symlink":
        real = tmp_path / "real.json"
        real.write_bytes(b"real")
        try:
            target.symlink_to(real)
        except OSError:
            pytest.skip("symlinks are unavailable")
    elif target_kind == "directory":
        target.mkdir()
    elif target_kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("special files are unavailable")
        try:
            os.mkfifo(target)
        except OSError:
            pytest.skip("FIFO creation is unavailable")

    reads: list[Path] = []
    original_read_bytes = evidence_module.Path.read_bytes

    def tracked_read_bytes(path: Path) -> bytes:
        reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(evidence_module.Path, "read_bytes", tracked_read_bytes)
    with pytest.raises(
        ExternalPublicationExecutionReconciliationEvidenceLoadError
    ) as raised:
        load_external_publication_execution_reconciliation(target)

    _assert_load_error(raised.value, "target")
    assert reads == []


def test_loader_requires_exact_concrete_path_type(tmp_path: Path) -> None:
    class PathChild(type(Path())):
        pass

    for candidate in (
        str(tmp_path / "string.json"),
        PathChild(tmp_path / "child.json"),
        tmp_path / "missing.json",
    ):
        with pytest.raises(
            ExternalPublicationExecutionReconciliationEvidenceLoadError
        ) as raised:
            load_external_publication_execution_reconciliation(candidate)  # type: ignore[arg-type]
        _assert_load_error(
            raised.value,
            "path_type" if type(candidate) is not type(Path()) else "target",
        )


@pytest.mark.parametrize(
    ("result", "expected"),
    [(_matched(), "matched"), (_mismatch(), "lineage_mismatch")],
)
def test_strict_loader_round_trips_matched_and_lineage_mismatch(
    tmp_path: Path,
    result: ExternalPublicationExecutionReconciliation,
    expected: str,
) -> None:
    path = tmp_path / f"{expected}.json"
    path.write_bytes(
        external_publication_execution_reconciliation_canonical_bytes(result)
    )
    before = path.read_bytes()

    loaded = load_external_publication_execution_reconciliation(path)

    assert type(loaded) is ExternalPublicationExecutionReconciliation
    assert loaded == result
    assert loaded.status == expected
    assert loaded.mismatched_fields == result.mismatched_fields
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("bad_kind", "expected_classification"),
    [
        ("duplicate", "parse"),
        ("nan", "parse"),
        ("infinity", "parse"),
        ("negative_infinity", "parse"),
        ("invalid_utf8", "parse"),
        ("bom", "parse"),
        ("malformed", "parse"),
        ("whitespace", "noncanonical"),
        ("pretty", "noncanonical"),
        ("wrong_order", "noncanonical"),
        ("escaped", "noncanonical"),
        ("trailing_newline", "noncanonical"),
        ("extra", "keys"),
        ("missing", "keys"),
    ],
)
def test_strict_loader_rejects_duplicate_constants_encoding_and_noncanonical_forms(
    tmp_path: Path,
    bad_kind: str,
    expected_classification: str,
) -> None:
    canonical = external_publication_execution_reconciliation_canonical_bytes(
        _matched()
    )
    value = json.loads(canonical)
    if bad_kind == "duplicate":
        contents = canonical.replace(
            b',"status":"matched"',
            b',"status":"matched","status":"matched"',
            1,
        )
    elif bad_kind == "nan":
        contents = canonical.replace(
            b'"mismatched_fields":[]',
            b'"mismatched_fields":NaN',
            1,
        )
    elif bad_kind == "infinity":
        contents = canonical.replace(
            b'"mismatched_fields":[]',
            b'"mismatched_fields":Infinity',
            1,
        )
    elif bad_kind == "negative_infinity":
        contents = canonical.replace(
            b'"mismatched_fields":[]',
            b'"mismatched_fields":-Infinity',
            1,
        )
    elif bad_kind == "invalid_utf8":
        contents = b"\xff"
    elif bad_kind == "bom":
        contents = b"\xef\xbb\xbf" + canonical
    elif bad_kind == "malformed":
        contents = b'{"schema_version"'
    elif bad_kind == "whitespace":
        contents = b" " + canonical
    elif bad_kind == "pretty":
        contents = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    elif bad_kind == "wrong_order":
        contents = json.dumps(
            {"status": value.pop("status"), **value},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    elif bad_kind == "escaped":
        contents = canonical.replace(b'"matched"', b'"\\u006datched"', 1)
    elif bad_kind == "trailing_newline":
        contents = canonical + b"\n"
    elif bad_kind == "extra":
        contents = canonical.replace(b"{", b'{"extra":1,', 1)
    else:
        del value["status"]
        contents = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    path = tmp_path / f"{bad_kind}.json"
    path.write_bytes(contents)
    original_read_bytes = evidence_module.Path.read_bytes
    reads: list[Path] = []

    def tracked_read_bytes(target: Path) -> bytes:
        reads.append(target)
        return original_read_bytes(target)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(evidence_module.Path, "read_bytes", tracked_read_bytes)
    try:
        with pytest.raises(
            ExternalPublicationExecutionReconciliationEvidenceLoadError
        ) as raised:
            load_external_publication_execution_reconciliation(path)
    finally:
        monkeypatch.undo()

    _assert_load_error(raised.value, expected_classification)
    assert reads == [path]
    assert path.read_bytes() == contents


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("schema_version", "wrong"),
        ("claim_sha256", "A" * 64),
        ("execution_evidence_sha256", "not-a-digest"),
        ("status", "published"),
        ("mismatched_fields", None),
        ("mismatched_fields", ["provider", "provider"]),
        ("mismatched_fields", ["provider", "publication_plan_sha256"]),
        ("mismatched_fields", ["unknown"]),
    ],
)
def test_loader_rejects_invalid_phase_283_model_invariants(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    value = json.loads(
        external_publication_execution_reconciliation_canonical_bytes(_matched())
    )
    value[field] = replacement
    if field == "status" and replacement == "published":
        value["mismatched_fields"] = []
    contents = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    path = tmp_path / f"invalid-{field}-{len(list(tmp_path.iterdir()))}.json"
    path.write_bytes(contents)

    with pytest.raises(
        ExternalPublicationExecutionReconciliationEvidenceLoadError
    ) as raised:
        load_external_publication_execution_reconciliation(path)

    _assert_load_error(raised.value, "result")
    assert path.read_bytes() == contents


@pytest.mark.parametrize("bad_value", ["provider", {"provider": 1}, 1, None])
def test_loader_requires_mismatched_fields_to_be_exact_json_list(
    tmp_path: Path,
    bad_value: object,
) -> None:
    value = _canonical_mapping(_mismatch())
    value["mismatched_fields"] = bad_value
    contents = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    path = tmp_path / f"not-list-{len(list(tmp_path.iterdir()))}.json"
    path.write_bytes(contents)

    with pytest.raises(
        ExternalPublicationExecutionReconciliationEvidenceLoadError
    ) as raised:
        load_external_publication_execution_reconciliation(path)

    _assert_load_error(raised.value, "result")


def test_loader_is_strictly_read_only_and_reads_target_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _mismatch()
    path = tmp_path / "readonly.json"
    contents = external_publication_execution_reconciliation_canonical_bytes(result)
    path.write_bytes(contents)
    reads: list[Path] = []
    original_read_bytes = evidence_module.Path.read_bytes

    def tracked_read_bytes(target: Path) -> bytes:
        reads.append(target)
        return original_read_bytes(target)

    monkeypatch.setattr(evidence_module.Path, "read_bytes", tracked_read_bytes)
    monkeypatch.setattr(
        evidence_module.Path,
        "write_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("loader repaired evidence")
        ),
    )
    monkeypatch.setattr(
        evidence_module.Path,
        "unlink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("loader deleted evidence")
        ),
    )

    assert load_external_publication_execution_reconciliation(path) == result
    assert reads == [path]
    assert path.read_bytes() == contents


def test_existing_durability_confirmation_failures_are_ambiguous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _matched()
    canonical = external_publication_execution_reconciliation_canonical_bytes(result)
    path = tmp_path / "existing.json"
    path.write_bytes(canonical)
    original_fsync = evidence_module.os.fsync
    fsync_calls = 0

    def failing_second_fsync(descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 1:
            raise OSError("existing file fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(evidence_module.os, "fsync", failing_second_fsync)

    with pytest.raises(
        ExternalPublicationExecutionReconciliationEvidencePersistenceError
    ) as raised:
        persist_external_publication_execution_reconciliation(path, result)

    _assert_persistence_error(raised.value, "ambiguous")
    assert path.read_bytes() == canonical


def test_predecessor_boundaries_and_phase_283_reconciliation_are_never_called(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forbidden_calls: list[str] = []

    def forbidden(name: str):
        def fail(*_args: object, **_kwargs: object) -> object:
            forbidden_calls.append(name)
            raise AssertionError(f"Phase 284 touched {name}")

        return fail

    boundaries = (
        (reconciliation_module, "reconcile_external_publication_execution"),
        (claim_module, "load_external_publication_attempt_claim"),
        (claim_module, "external_publication_attempt_claim_digest"),
        (claim_module, "claim_external_publication_attempt"),
        (execution_evidence_module, "load_external_publication_execution_result"),
        (execution_evidence_module, "external_publication_execution_result_digest"),
        (execution_evidence_module, "persist_external_publication_execution_result"),
        (execution_module, "execute_approved_external_publication"),
    )
    for module, name in boundaries:
        monkeypatch.setattr(module, name, forbidden(name), raising=False)

    path = tmp_path / "phase-284-only.json"
    result = _matched()
    persist_external_publication_execution_reconciliation(path, result)
    assert load_external_publication_execution_reconciliation(path) == result
    assert forbidden_calls == []

    source = inspect.getsource(evidence_module)
    for forbidden_name in (
        "reconcile_external_publication_execution(",
        "load_external_publication_attempt_claim(",
        "external_publication_attempt_claim_digest(",
        "load_external_publication_execution_result(",
        "external_publication_execution_result_digest(",
        "persist_external_publication_execution_result(",
        "execute_approved_external_publication(",
        "build_external_publication_plan(",
        "validate_external_publication_plan(",
        "load_publication_regeneration_export_reconciliation(",
        "reconcile_publication_regeneration_export(",
        "load_publication_regeneration_export_receipt(",
        "export_publication_regeneration_output(",
        "project_publication_regeneration_output(",
    ):
        assert forbidden_name not in source


def test_evidence_boundary_never_reads_output_or_runtime_external_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _matched()
    evidence_path = tmp_path / "evidence.json"
    output_path = tmp_path / "business-output.txt"
    output_path.write_bytes(b"business output must not be read")
    original_read_bytes = evidence_module.Path.read_bytes
    reads: list[Path] = []

    def guarded_read_bytes(path: Path) -> bytes:
        reads.append(path)
        if path == output_path:
            raise AssertionError("Phase 284 read the business-output file")
        return original_read_bytes(path)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden runtime input access")

    monkeypatch.setattr(evidence_module.Path, "read_bytes", guarded_read_bytes)
    for owner, name in (
        (os, "getenv"),
        (socket, "socket"),
        (time, "time"),
        (time, "time_ns"),
        (random, "random"),
        (secrets, "token_bytes"),
        (uuid, "uuid4"),
    ):
        monkeypatch.setattr(owner, name, forbidden)

    persist_external_publication_execution_reconciliation(evidence_path, result)
    assert load_external_publication_execution_reconciliation(evidence_path) == result
    assert output_path not in reads


def test_public_exports_are_available_and_cli_surface_is_not_part_of_evidence() -> None:
    public_names = (
        serialize_external_publication_execution_reconciliation_canonical,
        external_publication_execution_reconciliation_canonical_bytes,
        external_publication_execution_reconciliation_digest,
        persist_external_publication_execution_reconciliation,
        load_external_publication_execution_reconciliation,
    )
    assert all(callable(name) for name in public_names)
    assert set(dataclasses.asdict(_matched())) == {
        "schema_version",
        "claim_sha256",
        "execution_evidence_sha256",
        "status",
        "mismatched_fields",
    }
    assert "execute" not in evidence_module.__all__
    assert "cli" not in evidence_module.__all__
