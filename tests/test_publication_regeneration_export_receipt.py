"""Focused provider-free tests for Phase 274 receipt persistence."""

from __future__ import annotations

import hashlib
import json
import socket
import time
from pathlib import Path

import pytest

from ai_office.engine import (
    PublicationRegenerationExportReceipt,
    PublicationRegenerationExportReceiptConflictError,
    PublicationRegenerationExportReceiptError,
    PublicationRegenerationExportReceiptLoadError,
    PublicationRegenerationExportReceiptPersistenceError,
    load_publication_regeneration_export_receipt,
    persist_publication_regeneration_export_receipt,
    publication_regeneration_export_receipt_canonical_bytes,
    publication_regeneration_export_receipt_digest,
    serialize_publication_regeneration_export_receipt_canonical,
)
from ai_office.engine import publication_regeneration_export_receipt as receipt_module


@pytest.fixture
def receipt() -> PublicationRegenerationExportReceipt:
    return PublicationRegenerationExportReceipt(
        schema_version="publication-regeneration-export-receipt.v1",
        regeneration_id="regen-274-日本語😀",
        projection_sha256="a" * 64,
        readiness_record_sha256="b" * 64,
        result_record_sha256="c" * 64,
        source_audit_sha256="d" * 64,
        business_output_sha256="e" * 64,
        output_byte_length=17,
    )


def test_canonical_receipt_is_exact_compact_sorted_utf8_and_digest_pinned(
    receipt: PublicationRegenerationExportReceipt,
) -> None:
    canonical = serialize_publication_regeneration_export_receipt_canonical(receipt)
    expected = (
        '{"business_output_sha256":"'
        + "e" * 64
        + '","output_byte_length":17,"projection_sha256":"'
        + "a" * 64
        + '","readiness_record_sha256":"'
        + "b" * 64
        + '","regeneration_id":"regen-274-日本語😀","result_record_sha256":"'
        + "c" * 64
        + '","schema_version":"publication-regeneration-export-receipt.v1",'
        '"source_audit_sha256":"'
        + "d" * 64
        + '"}'
    )

    assert canonical == expected
    assert set(json.loads(canonical)) == {
        "schema_version",
        "regeneration_id",
        "projection_sha256",
        "readiness_record_sha256",
        "result_record_sha256",
        "source_audit_sha256",
        "business_output_sha256",
        "output_byte_length",
    }
    assert canonical == canonical.rstrip("\n")
    assert "\ufeff" not in canonical
    assert b"\xef\xbb\xbf" not in canonical.encode("utf-8")
    assert publication_regeneration_export_receipt_canonical_bytes(receipt) == (
        canonical.encode("utf-8")
    )
    assert publication_regeneration_export_receipt_digest(receipt) == hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def test_exact_phase272_receipt_type_is_required() -> None:
    values = {
        "schema_version": "publication-regeneration-export-receipt.v1",
        "regeneration_id": "regen-274",
        "projection_sha256": "a" * 64,
        "readiness_record_sha256": "b" * 64,
        "result_record_sha256": "c" * 64,
        "source_audit_sha256": "d" * 64,
        "business_output_sha256": "e" * 64,
        "output_byte_length": 3,
    }

    class ReceiptChild(PublicationRegenerationExportReceipt):
        pass

    child = object.__new__(ReceiptChild)
    for name, value in values.items():
        object.__setattr__(child, name, value)

    class Compatible:
        pass

    compatible = Compatible()
    for name, value in values.items():
        setattr(compatible, name, value)

    for candidate in (child, compatible, values):
        with pytest.raises(PublicationRegenerationExportReceiptError):
            serialize_publication_regeneration_export_receipt_canonical(candidate)  # type: ignore[arg-type]


def test_new_target_persists_exact_bytes_and_strict_load_returns_receipt(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
) -> None:
    path = tmp_path / "receipt.json"

    persist_publication_regeneration_export_receipt(path, receipt)

    expected = publication_regeneration_export_receipt_canonical_bytes(receipt)
    assert path.read_bytes() == expected
    assert load_publication_regeneration_export_receipt(path) == receipt


def test_new_persistence_orders_write_flush_file_fsync_close_parent_fsync(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "ordered.json"
    original_open = receipt_module.Path.open
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

    monkeypatch.setattr(receipt_module.Path, "open", tracked_open)
    monkeypatch.setattr(
        receipt_module.os,
        "fsync",
        lambda _descriptor: events.append("file_fsync"),
    )
    monkeypatch.setattr(
        receipt_module,
        "_fsync_receipt_directory",
        lambda _directory: events.append("parent_fsync"),
    )

    persist_publication_regeneration_export_receipt(path, receipt)

    assert events == ["write", "flush", "file_fsync", "close", "parent_fsync"]


def test_identical_target_is_idempotent_and_fsyncs_file_and_parent_without_rewrite(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "identical.json"
    contents = publication_regeneration_export_receipt_canonical_bytes(receipt)
    path.write_bytes(contents)
    events: list[str] = []
    monkeypatch.setattr(
        receipt_module.os,
        "fsync",
        lambda _descriptor: events.append("file_fsync"),
    )
    monkeypatch.setattr(
        receipt_module,
        "_fsync_receipt_directory",
        lambda _directory: events.append("parent_fsync"),
    )

    persist_publication_regeneration_export_receipt(path, receipt)

    assert path.read_bytes() == contents
    assert events == ["file_fsync", "parent_fsync"]


def test_conflict_and_race_conflict_never_change_existing_bytes(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
) -> None:
    path = tmp_path / "conflict.json"
    original = b"different bytes"
    path.write_bytes(original)

    with pytest.raises(PublicationRegenerationExportReceiptConflictError):
        persist_publication_regeneration_export_receipt(path, receipt)
    assert path.read_bytes() == original


def test_race_created_identical_target_is_idempotent_without_rewrite(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "race-identical.json"
    contents = publication_regeneration_export_receipt_canonical_bytes(receipt)
    original_open = receipt_module.Path.open
    injected = False
    modes: list[str] = []

    def race_open(
        target: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        nonlocal injected
        modes.append(mode)
        if target == path and mode == "xb" and not injected:
            injected = True
            with original_open(target, "wb") as handle:
                handle.write(contents)
            raise FileExistsError
        return original_open(target, mode, *args, **kwargs)

    monkeypatch.setattr(receipt_module.Path, "open", race_open)
    persist_publication_regeneration_export_receipt(path, receipt)

    assert modes.count("xb") == 1
    assert "wb" not in modes
    assert path.read_bytes() == contents


def test_race_created_conflicting_target_is_not_overwritten(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "race-conflict.json"
    original_open = receipt_module.Path.open
    injected = False
    conflicting = b"race-created-different"

    def race_open(
        target: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        nonlocal injected
        if target == path and mode == "xb" and not injected:
            injected = True
            with original_open(target, "wb") as handle:
                handle.write(conflicting)
            raise FileExistsError
        return original_open(target, mode, *args, **kwargs)

    monkeypatch.setattr(receipt_module.Path, "open", race_open)
    with pytest.raises(PublicationRegenerationExportReceiptConflictError):
        persist_publication_regeneration_export_receipt(path, receipt)

    assert path.read_bytes() == conflicting


@pytest.mark.parametrize("failure", ["write", "flush", "file_fsync", "close"])
def test_post_create_failures_are_ambiguous_and_retain_artifact(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    path = tmp_path / f"{failure}.json"
    original_open = receipt_module.Path.open
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
                raise OSError
            self.handle.flush()  # type: ignore[attr-defined]

        def fileno(self) -> int:
            return self.handle.fileno()  # type: ignore[attr-defined]

        def close(self) -> None:
            if failure == "close":
                raise OSError
            self.handle.close()  # type: ignore[attr-defined]

    def failure_open(
        target: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        nonlocal open_count
        if target == path and mode == "xb":
            open_count += 1
            return FailureHandle(original_open(target, mode, *args, **kwargs))
        return original_open(target, mode, *args, **kwargs)

    monkeypatch.setattr(receipt_module.Path, "open", failure_open)
    if failure == "file_fsync":
        def fail_fsync(_descriptor: int) -> None:
            raise OSError

        monkeypatch.setattr(receipt_module.os, "fsync", fail_fsync)

    with pytest.raises(PublicationRegenerationExportReceiptPersistenceError) as error:
        persist_publication_regeneration_export_receipt(path, receipt)

    assert error.value.detail.classification == "ambiguous"
    assert open_count == 1
    assert path.exists()
    if failure != "write":
        assert path.read_bytes() == (
            publication_regeneration_export_receipt_canonical_bytes(receipt)
        )


def test_parent_fsync_failure_is_ambiguous_and_retains_exact_artifact(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "parent-fsync.json"
    monkeypatch.setattr(
        receipt_module,
        "_fsync_receipt_directory",
        lambda _directory: (_ for _ in ()).throw(OSError()),
    )

    with pytest.raises(PublicationRegenerationExportReceiptPersistenceError) as error:
        persist_publication_regeneration_export_receipt(path, receipt)

    assert error.value.detail.classification == "ambiguous"
    assert path.read_bytes() == (
        publication_regeneration_export_receipt_canonical_bytes(receipt)
    )


def test_path_boundaries_reject_without_parent_creation_or_target_mutation(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
) -> None:
    missing_parent = tmp_path / "missing" / "receipt.json"
    with pytest.raises(PublicationRegenerationExportReceiptPersistenceError) as error:
        persist_publication_regeneration_export_receipt(missing_parent, receipt)
    assert error.value.detail.classification == "parent"
    assert not missing_parent.parent.exists()

    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(PublicationRegenerationExportReceiptPersistenceError):
        persist_publication_regeneration_export_receipt(directory, receipt)

    symlink = tmp_path / "symlink"
    symlink.symlink_to(directory, target_is_directory=True)
    with pytest.raises(PublicationRegenerationExportReceiptPersistenceError):
        persist_publication_regeneration_export_receipt(symlink, receipt)

    with pytest.raises(PublicationRegenerationExportReceiptPersistenceError) as error:
        persist_publication_regeneration_export_receipt(str(tmp_path), receipt)  # type: ignore[arg-type]
    assert error.value.detail.classification == "path_type"


def test_strict_loader_rejects_all_noncanonical_and_invalid_forms(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
) -> None:
    canonical = publication_regeneration_export_receipt_canonical_bytes(receipt)
    path = tmp_path / "load.json"
    cases = {
        "duplicate": canonical.replace(
            b'"schema_version":"publication-regeneration-export-receipt.v1"',
            b'"schema_version":"publication-regeneration-export-receipt.v1",'
            b'"schema_version":"publication-regeneration-export-receipt.v1"',
            1,
        ),
        "extra": canonical.replace(b"{", b'{"extra":1,', 1),
        "trailing": canonical + b"\n",
        "invalid_utf8": b"\xff",
        "malformed": b'{"schema_version"',
        "constant": canonical.replace(
            b'"output_byte_length":17', b'"output_byte_length":NaN'
        ),
        "pretty": json.dumps(
            json.loads(canonical), indent=2, ensure_ascii=False
        ).encode(),
        "wrong_order": (
            b'{"schema_version":"publication-regeneration-export-receipt.v1",'
        )
        + canonical.split(b",", 1)[1],
    }
    missing = json.loads(canonical)
    del missing["readiness_record_sha256"]
    cases["missing"] = json.dumps(missing, separators=(",", ":")).encode()

    for name, contents in cases.items():
        path.write_bytes(contents)
        with pytest.raises(PublicationRegenerationExportReceiptLoadError) as error:
            load_publication_regeneration_export_receipt(path)
        assert error.value.detail.classification in {
            "parse",
            "keys",
            "noncanonical",
        }, name
        assert path.read_bytes() == contents


def test_loader_rejects_invalid_model_values_and_error_does_not_leak_details(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
) -> None:
    canonical = publication_regeneration_export_receipt_canonical_bytes(receipt)
    path = tmp_path / "invalid.json"
    cases = {
        "schema": (b'"publication-regeneration-export-receipt.v1"', b'"wrong"'),
        "id": (receipt.regeneration_id.encode("utf-8"), b""),
        "digest": (b'"' + b"a" * 64 + b'"', b'"' + b"A" * 64 + b'"'),
        "length": (b'"output_byte_length":17', b'"output_byte_length":true'),
        "negative": (b'"output_byte_length":17', b'"output_byte_length":-1'),
    }

    for name, (old, new) in cases.items():
        contents = canonical.replace(old, new, 1)
        path.write_bytes(contents)
        with pytest.raises(PublicationRegenerationExportReceiptLoadError) as error:
            load_publication_regeneration_export_receipt(path)
        assert error.value.detail.classification == "receipt", name
        message = str(error.value)
        assert str(path) not in message
        assert receipt.projection_sha256 not in message
        assert "provider-secret-text" not in message


def test_loader_rejects_directory_symlink_wrong_path_and_does_not_repair(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(PublicationRegenerationExportReceiptLoadError):
        load_publication_regeneration_export_receipt(directory)

    symlink = tmp_path / "symlink"
    symlink.symlink_to(directory, target_is_directory=True)
    with pytest.raises(PublicationRegenerationExportReceiptLoadError):
        load_publication_regeneration_export_receipt(symlink)

    with pytest.raises(PublicationRegenerationExportReceiptLoadError) as error:
        load_publication_regeneration_export_receipt(str(tmp_path))  # type: ignore[arg-type]
    assert error.value.detail.classification == "path_type"

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_bytes(b"not-json")
    with pytest.raises(PublicationRegenerationExportReceiptLoadError):
        load_publication_regeneration_export_receipt(corrupt)
    assert corrupt.read_bytes() == b"not-json"
    assert receipt  # keep the fixture contract explicit for this read-only test


def test_receipt_boundary_has_no_provider_network_environment_clock_or_upstream_calls(
    tmp_path: Path,
    receipt: PublicationRegenerationExportReceipt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden external access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(receipt_module.os, "getenv", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    source = Path(receipt_module.__file__).read_text(encoding="utf-8")
    for forbidden_name in (
        "export_publication_regeneration_output",
        "project_publication_regeneration_output",
        "load_publication_regeneration_result",
        "load_publication_regeneration_readiness_record",
    ):
        assert forbidden_name not in source

    path = tmp_path / "pure.json"
    persist_publication_regeneration_export_receipt(path, receipt)
    assert load_publication_regeneration_export_receipt(path) == receipt


def test_only_explicit_receipt_sidecar_changes(
    tmp_path: Path, receipt: PublicationRegenerationExportReceipt
) -> None:
    lineage = tmp_path / "lineage"
    lineage.mkdir()
    prior: dict[str, bytes] = {}
    for name, contents in {
        "business-output.txt": b"business output",
        "state.json": b"state",
        "events.jsonl": b"events\n",
        "result.json": b"result",
        "readiness.json": b"readiness",
        "audit.json": b"audit",
        "claim.json": b"claim",
        "projection.json": b"projection",
    }.items():
        (lineage / name).write_bytes(contents)
        prior[name] = contents
    path = lineage / "receipt.json"

    persist_publication_regeneration_export_receipt(path, receipt)

    assert {name: (lineage / name).read_bytes() for name in prior} == prior
    assert path.read_bytes() == (
        publication_regeneration_export_receipt_canonical_bytes(receipt)
    )


@pytest.mark.parametrize(
    "export_name",
    (
        "serialize_publication_regeneration_export_receipt_canonical",
        "publication_regeneration_export_receipt_canonical_bytes",
        "publication_regeneration_export_receipt_digest",
        "persist_publication_regeneration_export_receipt",
        "load_publication_regeneration_export_receipt",
    ),
)
def test_phase274_public_exports_are_available_from_engine_package(
    export_name: str,
) -> None:
    import ai_office.engine as engine

    assert hasattr(engine, export_name)
