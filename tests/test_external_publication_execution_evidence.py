"""Focused provider-free tests for Phase 282 execution-result evidence."""

from __future__ import annotations

import dataclasses
import hashlib
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

import ai_office.engine.external_publication as publication_module
import ai_office.engine.external_publication_execution as execution_module
import ai_office.engine.external_publication_execution_evidence as evidence_module
from ai_office.engine import (
    ExternalPublicationExecutionAmbiguousError,
    ExternalPublicationExecutionError,
    ExternalPublicationExecutionEvidenceConflictError,
    ExternalPublicationExecutionEvidenceError,
    ExternalPublicationExecutionEvidenceFailureDetail,
    ExternalPublicationExecutionEvidenceLoadError,
    ExternalPublicationExecutionEvidencePersistenceError,
    ExternalPublicationExecutionResult,
    external_publication_execution_result_canonical_bytes,
    external_publication_execution_result_digest,
    load_external_publication_execution_result,
    persist_external_publication_execution_result,
    serialize_external_publication_execution_result_canonical,
)

_RESULT_SCHEMA = "external-publication-execution-result.v1"
_OUTPUT_LENGTH = 42
_CLAIM_DIGEST = "a" * 64
_PLAN_DIGEST = "b" * 64
_APPROVAL_DIGEST = "c" * 64
_OUTPUT_DIGEST = "d" * 64
_TARGET_DIGEST = "e" * 64
_PUBLICATION_ID = "公開-281-publication"


def _result() -> ExternalPublicationExecutionResult:
    return ExternalPublicationExecutionResult(
        schema_version=_RESULT_SCHEMA,
        regeneration_id="regen-281-execution",
        publication_attempt_claim_sha256=_CLAIM_DIGEST,
        publication_plan_sha256=_PLAN_DIGEST,
        publication_approval_sha256=_APPROVAL_DIGEST,
        business_output_sha256=_OUTPUT_DIGEST,
        output_byte_length=_OUTPUT_LENGTH,
        provider="future-provider",
        publication_target_sha256=_TARGET_DIGEST,
        publication_id=_PUBLICATION_ID,
        status="published",
    )


def _canonical_mapping(result: ExternalPublicationExecutionResult) -> dict[str, object]:
    return {
        "business_output_sha256": result.business_output_sha256,
        "output_byte_length": result.output_byte_length,
        "publication_approval_sha256": result.publication_approval_sha256,
        "publication_attempt_claim_sha256": result.publication_attempt_claim_sha256,
        "publication_id": result.publication_id,
        "publication_plan_sha256": result.publication_plan_sha256,
        "publication_target_sha256": result.publication_target_sha256,
        "provider": result.provider,
        "regeneration_id": result.regeneration_id,
        "schema_version": result.schema_version,
        "status": result.status,
    }


def _canonical_bytes(result: ExternalPublicationExecutionResult) -> bytes:
    return json.dumps(
        _canonical_mapping(result),
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
    assert type(error) is ExternalPublicationExecutionEvidenceError
    assert str(error) == "external publication execution evidence is invalid"
    assert type(error.detail) is ExternalPublicationExecutionEvidenceFailureDetail
    assert error.detail.classification == classification


def _assert_persistence_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationExecutionEvidencePersistenceError
    assert str(error) == ("external publication execution evidence persistence failed")
    assert type(error.detail) is ExternalPublicationExecutionEvidenceFailureDetail
    assert error.detail.classification == classification


def _assert_conflict_error(error: ValueError) -> None:
    assert type(error) is ExternalPublicationExecutionEvidenceConflictError
    assert str(error) == ("external publication execution evidence persistence failed")
    assert type(error.detail) is ExternalPublicationExecutionEvidenceFailureDetail
    assert error.detail.classification == "conflict"


def _assert_load_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationExecutionEvidenceLoadError
    assert str(error) == ("external publication execution evidence could not be loaded")
    assert type(error.detail) is ExternalPublicationExecutionEvidenceFailureDetail
    assert error.detail.classification == classification


def test_result_evidence_has_exact_frozen_eleven_field_identity() -> None:
    result = _result()

    assert type(result) is ExternalPublicationExecutionResult
    assert tuple(field.name for field in fields(result)) == (
        "schema_version",
        "regeneration_id",
        "publication_attempt_claim_sha256",
        "publication_plan_sha256",
        "publication_approval_sha256",
        "business_output_sha256",
        "output_byte_length",
        "provider",
        "publication_target_sha256",
        "publication_id",
        "status",
    )
    assert result.status == "published"
    with pytest.raises(FrozenInstanceError):
        result.status = "other"  # type: ignore[misc]


def test_canonical_json_is_exact_eleven_key_compact_sorted_utf8_evidence() -> None:
    result = _result()
    canonical = serialize_external_publication_execution_result_canonical(result)
    expected = (
        '{"business_output_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",'
        '"output_byte_length":42,'
        '"provider":"future-provider",'
        '"publication_approval_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",'
        '"publication_attempt_claim_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        f'"publication_id":"{_PUBLICATION_ID}",'
        '"publication_plan_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
        '"publication_target_sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",'
        '"regeneration_id":"regen-281-execution",'
        '"schema_version":"external-publication-execution-result.v1",'
        '"status":"published"}'
    )

    assert canonical == expected
    assert json.loads(canonical) == _canonical_mapping(result)
    assert set(json.loads(canonical)) == {
        "schema_version",
        "regeneration_id",
        "publication_attempt_claim_sha256",
        "publication_plan_sha256",
        "publication_approval_sha256",
        "business_output_sha256",
        "output_byte_length",
        "provider",
        "publication_target_sha256",
        "publication_id",
        "status",
    }
    assert "\\u516c" not in canonical
    assert " " not in canonical
    assert "\n" not in canonical
    assert canonical.encode("utf-8") == _canonical_bytes(result)


def test_canonical_bytes_and_digest_are_exact_sha256_of_utf8_bytes() -> None:
    result = _result()
    canonical = _canonical_bytes(result)

    assert external_publication_execution_result_canonical_bytes(result) == canonical
    assert external_publication_execution_result_digest(result) == (
        hashlib.sha256(canonical).hexdigest()
    )
    assert len(external_publication_execution_result_digest(result)) == 64
    assert external_publication_execution_result_digest(result).islower()


def test_unicode_publication_id_is_not_normalized_or_ascii_escaped() -> None:
    result = dataclasses.replace(
        _result(),
        publication_id="公開先-é-日本語",
    )
    canonical = external_publication_execution_result_canonical_bytes(result)

    assert "公開先-é-日本語".encode() in canonical
    assert b"\\u516c" not in canonical


@pytest.mark.parametrize("operation", ["serialize", "bytes", "digest", "persist"])
def test_public_boundaries_require_exact_result_type(
    tmp_path: Path,
    operation: str,
) -> None:
    result = _result()

    class ResultChild(ExternalPublicationExecutionResult):
        pass

    candidates = (
        _forged_instance(ResultChild, result),
        SimpleNamespace(**result.__dict__),
        result.__dict__.copy(),
    )
    for candidate in candidates:
        if operation == "serialize":
            with pytest.raises(ExternalPublicationExecutionEvidenceError) as raised:
                serialize_external_publication_execution_result_canonical(candidate)  # type: ignore[arg-type]
            _assert_evidence_error(raised.value, "result_type")
        elif operation == "bytes":
            with pytest.raises(ExternalPublicationExecutionEvidenceError) as raised:
                external_publication_execution_result_canonical_bytes(candidate)  # type: ignore[arg-type]
            _assert_evidence_error(raised.value, "result_type")
        elif operation == "digest":
            with pytest.raises(ExternalPublicationExecutionEvidenceError) as raised:
                external_publication_execution_result_digest(candidate)  # type: ignore[arg-type]
            _assert_evidence_error(raised.value, "result_type")
        else:
            path = tmp_path / f"{len(list(tmp_path.iterdir()))}.json"
            with pytest.raises(ExternalPublicationExecutionEvidenceError) as raised:
                persist_external_publication_execution_result(path, candidate)  # type: ignore[arg-type]
            _assert_evidence_error(raised.value, "result_type")
            assert not path.exists()


def test_forged_exact_result_is_revalidated_before_persistence_mutation(
    tmp_path: Path,
) -> None:
    forged = _forged_instance(ExternalPublicationExecutionResult, _result())
    object.__setattr__(forged, "status", "not-published")
    path = tmp_path / "forged.json"

    with pytest.raises(ExternalPublicationExecutionEvidenceError) as raised:
        persist_external_publication_execution_result(path, forged)  # type: ignore[arg-type]

    _assert_evidence_error(raised.value, "result")
    assert not path.exists()


def test_new_target_persists_exact_canonical_bytes_and_loads_exact_result(
    tmp_path: Path,
) -> None:
    result = _result()
    path = tmp_path / "execution-result.json"

    assert persist_external_publication_execution_result(path, result) is None
    assert path.read_bytes() == external_publication_execution_result_canonical_bytes(
        result
    )
    loaded = load_external_publication_execution_result(path)
    assert type(loaded) is ExternalPublicationExecutionResult
    assert loaded == result


def test_new_persistence_orders_write_full_check_flush_file_fsync_close_dir_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _result()
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

    persist_external_publication_execution_result(path, result)

    assert events == ["write", "flush", "fileno", "file_fsync", "close", "parent_fsync"]
    assert path.read_bytes() == external_publication_execution_result_canonical_bytes(
        result
    )


@pytest.mark.parametrize(
    "failure",
    ["write", "flush", "file_fsync", "close", "parent_fsync"],
)
def test_post_create_failures_are_ambiguous_retain_artifact_and_do_not_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    result = _result()
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
                self.handle.close()  # type: ignore[attr-defined]
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
                OSError("directory fsync failure")
            ),
        )

    with pytest.raises(ExternalPublicationExecutionEvidencePersistenceError) as raised:
        persist_external_publication_execution_result(path, result)

    _assert_persistence_error(raised.value, "ambiguous")
    assert open_count == 1
    assert path.exists()
    if failure != "write":
        assert (
            path.read_bytes()
            == external_publication_execution_result_canonical_bytes(result)
        )


def test_persistence_does_not_automatically_retry_after_ambiguous_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _result()
    path = tmp_path / "ambiguous.json"
    original_open = evidence_module.Path.open
    open_count = 0

    def failure_open(
        target: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        nonlocal open_count
        if target == path and mode == "xb":
            open_count += 1
            handle = original_open(target, mode, *args, **kwargs)
            handle.close()
            raise OSError("create boundary failure")
        return original_open(target, mode, *args, **kwargs)

    monkeypatch.setattr(evidence_module.Path, "open", failure_open)
    with pytest.raises(ExternalPublicationExecutionEvidencePersistenceError):
        persist_external_publication_execution_result(path, result)
    assert open_count == 1


def test_identical_existing_canonical_target_is_idempotent_without_rewrite_and_fsyncs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _result()
    path = tmp_path / "identical.json"
    contents = external_publication_execution_result_canonical_bytes(result)
    path.write_bytes(contents)
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

    assert persist_external_publication_execution_result(path, result) is None

    assert events == [
        "xb",
        "read_bytes",
        "rb",
        "rb",
        "file_fsync",
        "parent_fsync",
    ]
    assert original_read_bytes(path) == contents


def test_existing_different_and_noncanonical_targets_conflict_unchanged(
    tmp_path: Path,
) -> None:
    result = _result()
    canonical = external_publication_execution_result_canonical_bytes(result)
    variants = {
        "different": b"different evidence bytes",
        "noncanonical": b" " + canonical,
    }
    for name, existing in variants.items():
        path = tmp_path / f"{name}.json"
        path.write_bytes(existing)

        with pytest.raises(ExternalPublicationExecutionEvidenceConflictError) as raised:
            persist_external_publication_execution_result(path, result)

        _assert_conflict_error(raised.value)
        assert path.read_bytes() == existing


def test_persistence_requires_existing_parent_and_exact_path_type(
    tmp_path: Path,
) -> None:
    result = _result()
    missing_parent = tmp_path / "missing-parent" / "result.json"
    with pytest.raises(ExternalPublicationExecutionEvidencePersistenceError) as raised:
        persist_external_publication_execution_result(missing_parent, result)
    _assert_persistence_error(raised.value, "parent")
    assert not missing_parent.parent.exists()

    with pytest.raises(ExternalPublicationExecutionEvidencePersistenceError) as raised:
        persist_external_publication_execution_result(
            str(tmp_path / "result.json"),  # type: ignore[arg-type]
            result,
        )
    _assert_persistence_error(raised.value, "path_type")


@pytest.mark.parametrize("target_kind", ["symlink", "directory", "fifo"])
def test_persistence_rejects_symlink_directory_and_nonregular_target(
    tmp_path: Path,
    target_kind: str,
) -> None:
    result = _result()
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

    with pytest.raises(ExternalPublicationExecutionEvidencePersistenceError) as raised:
        persist_external_publication_execution_result(target, result)
    _assert_persistence_error(raised.value, "target")


@pytest.mark.parametrize("bad_kind", ["missing", "symlink", "directory", "fifo"])
def test_loader_rejects_missing_symlink_directory_and_fifo_without_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_kind: str,
) -> None:
    target = tmp_path / bad_kind
    if bad_kind == "symlink":
        real = tmp_path / "real.json"
        real.write_bytes(b"real")
        try:
            target.symlink_to(real)
        except OSError:
            pytest.skip("symlinks are unavailable")
    elif bad_kind == "directory":
        target.mkdir()
    elif bad_kind == "fifo":
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
    with pytest.raises(ExternalPublicationExecutionEvidenceLoadError) as raised:
        load_external_publication_execution_result(target)
    _assert_load_error(raised.value, "target")
    assert reads == []


def test_loader_requires_exact_path_type(tmp_path: Path) -> None:
    with pytest.raises(ExternalPublicationExecutionEvidenceLoadError) as raised:
        load_external_publication_execution_result(str(tmp_path / "result.json"))  # type: ignore[arg-type]
    _assert_load_error(raised.value, "path_type")


@pytest.mark.parametrize(
    "bad_kind, expected_classification",
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
def test_strict_loader_rejects_noncanonical_and_malformed_forms(
    tmp_path: Path,
    bad_kind: str,
    expected_classification: str,
) -> None:
    canonical = external_publication_execution_result_canonical_bytes(_result())
    value = json.loads(canonical)
    if bad_kind == "duplicate":
        contents = canonical.replace(
            b',"status":"published"',
            b',"status":"published","status":"published"',
            1,
        )
    elif bad_kind == "nan":
        contents = canonical.replace(
            b'"output_byte_length":42',
            b'"output_byte_length":NaN',
            1,
        )
    elif bad_kind == "infinity":
        contents = canonical.replace(
            b'"output_byte_length":42',
            b'"output_byte_length":Infinity',
            1,
        )
    elif bad_kind == "negative_infinity":
        contents = canonical.replace(
            b'"output_byte_length":42',
            b'"output_byte_length":-Infinity',
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
        contents = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    elif bad_kind == "trailing_newline":
        contents = canonical + b"\n"
    elif bad_kind == "extra":
        contents = canonical.replace(b"{", b'{"extra":1,', 1)
    else:
        del value["publication_id"]
        contents = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    path = tmp_path / f"{bad_kind}.json"
    path.write_bytes(contents)
    reads: list[Path] = []
    original_read_bytes = evidence_module.Path.read_bytes

    def tracked_read_bytes(target: Path) -> bytes:
        reads.append(target)
        return original_read_bytes(target)

    # The loader is allowed one and only one read of the evidence target.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(evidence_module.Path, "read_bytes", tracked_read_bytes)
    try:
        with pytest.raises(ExternalPublicationExecutionEvidenceLoadError) as raised:
            load_external_publication_execution_result(path)
    finally:
        monkeypatch.undo()

    _assert_load_error(raised.value, expected_classification)
    assert reads == [path]
    assert path.read_bytes() == contents


@pytest.mark.parametrize(
    "field, replacement",
    [
        ("schema_version", "wrong"),
        ("regeneration_id", ""),
        ("regeneration_id", "regen with spaces"),
        ("publication_attempt_claim_sha256", "A" * 64),
        ("publication_plan_sha256", "not-a-digest"),
        ("publication_approval_sha256", "g" * 64),
        ("business_output_sha256", 1),
        ("output_byte_length", True),
        ("output_byte_length", -1),
        ("provider", "provider with spaces"),
        ("publication_target_sha256", "E" * 64),
        ("publication_id", " publication"),
        ("publication_id", "publication\nsecret"),
        ("status", "completed"),
    ],
)
def test_loader_rejects_invalid_result_fields_without_detail_leaks(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    value = json.loads(external_publication_execution_result_canonical_bytes(_result()))
    value[field] = replacement
    contents = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    path = tmp_path / f"invalid-{field}-{len(list(tmp_path.iterdir()))}.json"
    path.write_bytes(contents)

    with pytest.raises(ExternalPublicationExecutionEvidenceLoadError) as raised:
        load_external_publication_execution_result(path)
    _assert_load_error(raised.value, "result")
    assert str(path) not in str(raised.value)
    assert _PUBLICATION_ID not in str(raised.value)
    assert _OUTPUT_DIGEST not in str(raised.value)


def test_loader_round_trip_returns_exact_result_and_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _result()
    path = tmp_path / "round-trip.json"
    path.write_bytes(external_publication_execution_result_canonical_bytes(result))
    before = path.read_bytes()
    reads: list[Path] = []
    original_read_bytes = evidence_module.Path.read_bytes

    def tracked_read_bytes(target: Path) -> bytes:
        reads.append(target)
        return original_read_bytes(target)

    monkeypatch.setattr(evidence_module.Path, "read_bytes", tracked_read_bytes)
    loaded = load_external_publication_execution_result(path)

    assert type(loaded) is ExternalPublicationExecutionResult
    assert loaded == result
    assert reads == [path]
    assert original_read_bytes(path) == before


def test_serializer_and_persistence_never_call_phase_281_execution_or_predecessors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forbidden_names = (
        "execute_approved_external_publication",
        "claim_external_publication_attempt",
        "load_external_publication_attempt_claim",
        "validate_external_publication_plan",
        "validate_external_publication_approval",
        "build_external_publication_plan",
        "build_external_publication_attempt_claim",
        "load_publication_regeneration_export_reconciliation",
        "reconcile_publication_regeneration_export",
        "load_publication_regeneration_export_receipt",
        "export_publication_regeneration_output",
        "project_publication_regeneration_output",
    )

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Phase 282 touched a forbidden predecessor boundary")

    for name in forbidden_names:
        monkeypatch.setattr(execution_module, name, forbidden, raising=False)
        monkeypatch.setattr(publication_module, name, forbidden, raising=False)
        monkeypatch.setattr(evidence_module, name, forbidden, raising=False)

    path = tmp_path / "evidence.json"
    result = _result()
    persist_external_publication_execution_result(path, result)
    assert load_external_publication_execution_result(path) == result


def test_persistence_and_load_do_not_access_environment_network_clock_random_or_uuid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden runtime access")

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

    path = tmp_path / "evidence.json"
    result = _result()
    persist_external_publication_execution_result(path, result)
    assert load_external_publication_execution_result(path) == result


def test_persistence_does_not_read_an_exported_business_output_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _result()
    evidence_path = tmp_path / "evidence.json"
    output_path = tmp_path / "business-output.txt"
    output_path.write_bytes(b"raw business output must not be read")
    original_read_bytes = evidence_module.Path.read_bytes
    reads: list[Path] = []

    def guarded_read_bytes(path: Path) -> bytes:
        reads.append(path)
        if path == output_path:
            raise AssertionError("Phase 282 read the exported output")
        return original_read_bytes(path)

    monkeypatch.setattr(evidence_module.Path, "read_bytes", guarded_read_bytes)
    persist_external_publication_execution_result(evidence_path, result)
    assert load_external_publication_execution_result(evidence_path) == result
    assert output_path not in reads


def test_public_exports_are_available_and_evidence_has_no_execution_result_wrapper():
    result = _result()
    assert callable(serialize_external_publication_execution_result_canonical)
    assert callable(external_publication_execution_result_canonical_bytes)
    assert callable(external_publication_execution_result_digest)
    assert callable(persist_external_publication_execution_result)
    assert callable(load_external_publication_execution_result)
    assert set(dataclasses.asdict(result)) == set(_canonical_mapping(result))
    assert "evidence_id" not in dataclasses.asdict(result)
    assert "timestamp" not in dataclasses.asdict(result)
    assert "path" not in dataclasses.asdict(result)
    assert "destination_id" not in dataclasses.asdict(result)
    assert "approval_id" not in dataclasses.asdict(result)
    assert "output" not in dataclasses.asdict(result)


def test_execution_error_types_are_not_used_as_public_evidence_errors() -> None:
    assert issubclass(
        ExternalPublicationExecutionAmbiguousError,
        ExternalPublicationExecutionError,
    )
    assert not issubclass(
        ExternalPublicationExecutionEvidenceError,
        ExternalPublicationExecutionError,
    )
