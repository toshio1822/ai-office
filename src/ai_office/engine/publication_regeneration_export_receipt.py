"""Canonical durable evidence for an already-produced Phase 272 receipt.

Phase 274 stores only the exact in-memory
:class:`PublicationRegenerationExportReceipt` returned by Phase 272.  This
module does not export, project, inspect, reconcile, or otherwise reinterpret
business output.  It has no provider, network, environment, clock, or
workflow-state access.
"""

from __future__ import annotations

import errno
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import NoReturn

from ai_office.engine.publication_regeneration_export import (
    PublicationRegenerationExportReceipt,
)

_RECEIPT_ERROR_MESSAGE = "publication regeneration export receipt is invalid"
_PERSISTENCE_ERROR_MESSAGE = (
    "publication regeneration export receipt persistence failed"
)
_LOAD_ERROR_MESSAGE = (
    "publication regeneration export receipt could not be loaded"
)
_RECEIPT_SCHEMA_VERSION = "publication-regeneration-export-receipt.v1"
_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "regeneration_id",
        "projection_sha256",
        "readiness_record_sha256",
        "result_record_sha256",
        "source_audit_sha256",
        "business_output_sha256",
        "output_byte_length",
    }
)
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class PublicationRegenerationExportReceiptFailureDetail:
    """Safe classification for one rejected or ambiguous receipt operation."""

    classification: str


class PublicationRegenerationExportReceiptError(ValueError):
    """Raised when a Phase 272 receipt is not exact receipt evidence."""

    def __init__(self, classification: str = "receipt") -> None:
        super().__init__(_RECEIPT_ERROR_MESSAGE)
        self.detail = PublicationRegenerationExportReceiptFailureDetail(
            classification
        )


class PublicationRegenerationExportReceiptPersistenceError(
    PublicationRegenerationExportReceiptError
):
    """Raised when receipt persistence cannot be proven durable."""

    def __init__(self, classification: str = "persistence") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = PublicationRegenerationExportReceiptFailureDetail(
            classification
        )


class PublicationRegenerationExportReceiptConflictError(
    PublicationRegenerationExportReceiptPersistenceError
):
    """Raised when an existing target contains different bytes."""


class PublicationRegenerationExportReceiptLoadError(
    PublicationRegenerationExportReceiptError
):
    """Raised when a sidecar is not exact canonical receipt evidence."""

    def __init__(self, classification: str = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = PublicationRegenerationExportReceiptFailureDetail(
            classification
        )


def serialize_publication_regeneration_export_receipt_canonical(
    receipt: PublicationRegenerationExportReceipt,
) -> str:
    """Serialize one exact Phase 272 receipt as compact deterministic JSON."""
    validated = _validated_receipt(receipt)
    return _serialize_validated_receipt(validated)


def publication_regeneration_export_receipt_canonical_bytes(
    receipt: PublicationRegenerationExportReceipt,
) -> bytes:
    """Return the exact canonical receipt JSON encoded as UTF-8 bytes."""
    try:
        return serialize_publication_regeneration_export_receipt_canonical(
            receipt
        ).encode("utf-8")
    except UnicodeError:
        _raise_receipt("encoding")


def publication_regeneration_export_receipt_digest(
    receipt: PublicationRegenerationExportReceipt,
) -> str:
    """Return SHA-256 over the exact canonical receipt UTF-8 bytes."""
    return sha256(
        publication_regeneration_export_receipt_canonical_bytes(receipt)
    ).hexdigest()


def persist_publication_regeneration_export_receipt(
    path: Path,
    receipt: PublicationRegenerationExportReceipt,
) -> None:
    """Create or idempotently re-persist one explicit receipt sidecar.

    Receipt validation and canonical byte derivation happen before any
    filesystem mutation.  Once exclusive creation succeeds, every later
    failure is reported as ambiguous and the created artifact is retained.
    """
    contents = publication_regeneration_export_receipt_canonical_bytes(receipt)
    _validate_persistence_path(path)

    try:
        handle = path.open("xb")
    except FileExistsError:
        _persist_existing_receipt(path, contents)
        return
    except OSError as error:
        if error.errno == errno.EEXIST:
            _persist_existing_receipt(path, contents)
            return
        _raise_persistence("create")
    except Exception:
        _raise_persistence("create")

    _persist_new_receipt(handle, path, contents)


def load_publication_regeneration_export_receipt(
    path: Path,
) -> PublicationRegenerationExportReceipt:
    """Strictly load one exact canonical receipt sidecar without mutation."""
    _validate_load_path(path)
    try:
        contents = path.read_bytes()
    except Exception:
        _raise_load("target")

    try:
        value = json.loads(
            contents.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_receipt_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
        receipt = _parse_receipt(value)
        if _serialize_validated_receipt(receipt).encode("utf-8") != contents:
            _raise_load("noncanonical")
        return receipt
    except PublicationRegenerationExportReceiptLoadError:
        raise
    except Exception:
        _raise_load("parse")


def _validated_receipt(
    receipt: object,
) -> PublicationRegenerationExportReceipt:
    if type(receipt) is not PublicationRegenerationExportReceipt:
        _raise_receipt("receipt_type")
    try:
        # Reconstructing the exact Phase 272 public model keeps its own
        # schema, identity, digest, and byte-length validation authoritative.
        return PublicationRegenerationExportReceipt(
            schema_version=receipt.schema_version,  # type: ignore[union-attr]
            regeneration_id=receipt.regeneration_id,  # type: ignore[union-attr]
            projection_sha256=receipt.projection_sha256,  # type: ignore[union-attr]
            readiness_record_sha256=receipt.readiness_record_sha256,  # type: ignore[union-attr]
            result_record_sha256=receipt.result_record_sha256,  # type: ignore[union-attr]
            source_audit_sha256=receipt.source_audit_sha256,  # type: ignore[union-attr]
            business_output_sha256=receipt.business_output_sha256,  # type: ignore[union-attr]
            output_byte_length=receipt.output_byte_length,  # type: ignore[union-attr]
        )
    except Exception:
        _raise_receipt("receipt")


def _serialize_validated_receipt(
    receipt: PublicationRegenerationExportReceipt,
) -> str:
    try:
        return json.dumps(
            {
                "business_output_sha256": receipt.business_output_sha256,
                "output_byte_length": receipt.output_byte_length,
                "projection_sha256": receipt.projection_sha256,
                "readiness_record_sha256": receipt.readiness_record_sha256,
                "regeneration_id": receipt.regeneration_id,
                "result_record_sha256": receipt.result_record_sha256,
                "schema_version": receipt.schema_version,
                "source_audit_sha256": receipt.source_audit_sha256,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except Exception:
        _raise_receipt("serialization")


def _validate_persistence_path(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_persistence("path_type")
    try:
        if not path.parent.exists() or not path.parent.is_dir():  # type: ignore[union-attr]
            _raise_persistence("parent")
        if (
            path.is_dir()  # type: ignore[union-attr]
            or path.is_symlink()  # type: ignore[union-attr]
            or (path.exists() and not path.is_file())  # type: ignore[union-attr]
        ):
            _raise_persistence("target")
    except PublicationRegenerationExportReceiptPersistenceError:
        raise
    except OSError:
        _raise_persistence("target")
    except Exception:
        _raise_persistence("target")


def _validate_load_path(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_load("path_type")
    try:
        if (
            path.is_dir()  # type: ignore[union-attr]
            or path.is_symlink()  # type: ignore[union-attr]
            or (path.exists() and not path.is_file())  # type: ignore[union-attr]
        ):
            _raise_load("target")
    except PublicationRegenerationExportReceiptLoadError:
        raise
    except OSError:
        _raise_load("target")
    except Exception:
        _raise_load("target")


def _persist_new_receipt(
    handle: object,
    path: Path,
    contents: bytes,
) -> None:
    try:
        with _receipt_handle_scope(handle) as active_handle:
            written = active_handle.write(contents)  # type: ignore[union-attr]
            if written != len(contents):
                raise OSError("short write")
            active_handle.flush()  # type: ignore[union-attr]
            os.fsync(active_handle.fileno())  # type: ignore[union-attr]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_receipt_directory(path.parent)
    except Exception:
        _raise_persistence("ambiguous")


@contextmanager
def _receipt_handle_scope(handle: object) -> Iterator[object]:
    enter = getattr(handle, "__enter__", None)
    exit_ = getattr(handle, "__exit__", None)
    if callable(enter) and callable(exit_):
        with handle as active_handle:  # type: ignore[union-attr]
            yield active_handle
        return

    try:
        yield handle
    finally:
        handle.close()  # type: ignore[union-attr]


def _persist_existing_receipt(path: Path, contents: bytes) -> None:
    try:
        if path.is_dir() or path.is_symlink() or not path.is_file():
            _raise_persistence("target")
        existing = path.read_bytes()
    except PublicationRegenerationExportReceiptPersistenceError:
        raise
    except Exception:
        _raise_persistence("target")

    if existing != contents:
        _raise_conflict()

    try:
        handle = path.open("rb")
    except Exception:
        _raise_persistence("target")

    try:
        with _receipt_handle_scope(handle) as active_handle:
            os.fsync(active_handle.fileno())  # type: ignore[union-attr]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_receipt_directory(path.parent)
    except Exception:
        _raise_persistence("ambiguous")


def _fsync_receipt_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_receipt(value: object) -> PublicationRegenerationExportReceipt:
    if type(value) is not dict or frozenset(value) != _RECEIPT_KEYS:
        _raise_load("keys")
    try:
        return PublicationRegenerationExportReceipt(
            schema_version=value["schema_version"],
            regeneration_id=value["regeneration_id"],
            projection_sha256=value["projection_sha256"],
            readiness_record_sha256=value["readiness_record_sha256"],
            result_record_sha256=value["result_record_sha256"],
            source_audit_sha256=value["source_audit_sha256"],
            business_output_sha256=value["business_output_sha256"],
            output_byte_length=value["output_byte_length"],
        )
    except Exception:
        _raise_load("receipt")


def _reject_duplicate_receipt_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateReceiptKeyError
        result[key] = value
    return result


class _DuplicateReceiptKeyError(ValueError):
    pass


def _reject_nonstandard_json_constant(constant: str) -> NoReturn:
    raise _NonStandardJSONConstantError


class _NonStandardJSONConstantError(ValueError):
    pass


def _raise_receipt(classification: str) -> NoReturn:
    raise PublicationRegenerationExportReceiptError(classification) from None


def _raise_persistence(classification: str) -> NoReturn:
    raise PublicationRegenerationExportReceiptPersistenceError(
        classification
    ) from None


def _raise_conflict() -> NoReturn:
    raise PublicationRegenerationExportReceiptConflictError("conflict") from None


def _raise_load(classification: str) -> NoReturn:
    raise PublicationRegenerationExportReceiptLoadError(classification) from None


__all__ = [
    "PublicationRegenerationExportReceiptConflictError",
    "PublicationRegenerationExportReceiptError",
    "PublicationRegenerationExportReceiptFailureDetail",
    "PublicationRegenerationExportReceiptLoadError",
    "PublicationRegenerationExportReceiptPersistenceError",
    "load_publication_regeneration_export_receipt",
    "persist_publication_regeneration_export_receipt",
    "publication_regeneration_export_receipt_canonical_bytes",
    "publication_regeneration_export_receipt_digest",
    "serialize_publication_regeneration_export_receipt_canonical",
]
