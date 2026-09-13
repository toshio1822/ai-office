"""Canonical durable evidence for one Phase 275 reconciliation result.

Phase 276 persists only an already-derived
:class:`PublicationRegenerationExportReconciliation`.  This module does not
observe receipt or export artifacts and does not rerun reconciliation.
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

from ai_office.engine.publication_regeneration_export_reconciliation import (
    PublicationRegenerationExportReconciliation,
)

_EVIDENCE_ERROR_MESSAGE = (
    "publication regeneration export reconciliation evidence is invalid"
)
_PERSISTENCE_ERROR_MESSAGE = (
    "publication regeneration export reconciliation evidence persistence failed"
)
_LOAD_ERROR_MESSAGE = (
    "publication regeneration export reconciliation evidence could not be loaded"
)
_SCHEMA_VERSION = "publication-regeneration-export-reconciliation.v1"
_EVIDENCE_KEYS = frozenset(
    {
        "schema_version",
        "regeneration_id",
        "receipt_sha256",
        "status",
        "expected_business_output_sha256",
        "expected_output_byte_length",
        "observed_business_output_sha256",
        "observed_output_byte_length",
    }
)
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class PublicationRegenerationExportReconciliationEvidenceFailureDetail:
    """Safe classification for one evidence operation failure."""

    classification: str


class PublicationRegenerationExportReconciliationEvidenceError(ValueError):
    """Raised when a Phase 275 result is not exact evidence."""

    def __init__(self, classification: str = "reconciliation") -> None:
        super().__init__(_EVIDENCE_ERROR_MESSAGE)
        self.detail = (
            PublicationRegenerationExportReconciliationEvidenceFailureDetail(
                classification
            )
        )


class PublicationRegenerationExportReconciliationEvidencePersistenceError(
    PublicationRegenerationExportReconciliationEvidenceError
):
    """Raised when evidence persistence cannot be proven durable."""

    def __init__(self, classification: str = "persistence") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = (
            PublicationRegenerationExportReconciliationEvidenceFailureDetail(
                classification
            )
        )


class PublicationRegenerationExportReconciliationEvidenceConflictError(
    PublicationRegenerationExportReconciliationEvidencePersistenceError
):
    """Raised when an existing target contains different bytes."""


class PublicationRegenerationExportReconciliationEvidenceLoadError(
    PublicationRegenerationExportReconciliationEvidenceError
):
    """Raised when a target is not exact canonical reconciliation evidence."""

    def __init__(self, classification: str = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = (
            PublicationRegenerationExportReconciliationEvidenceFailureDetail(
                classification
            )
        )


def serialize_publication_regeneration_export_reconciliation_canonical(
    reconciliation: PublicationRegenerationExportReconciliation,
) -> str:
    """Serialize one exact Phase 275 result as compact deterministic JSON."""
    validated = _validated_reconciliation(reconciliation)
    return _serialize_validated_reconciliation(validated)


def publication_regeneration_export_reconciliation_canonical_bytes(
    reconciliation: PublicationRegenerationExportReconciliation,
) -> bytes:
    """Return exact canonical reconciliation JSON encoded as UTF-8 bytes."""
    try:
        return serialize_publication_regeneration_export_reconciliation_canonical(
            reconciliation
        ).encode("utf-8")
    except UnicodeError:
        _raise_evidence("encoding")


def publication_regeneration_export_reconciliation_digest(
    reconciliation: PublicationRegenerationExportReconciliation,
) -> str:
    """Return SHA-256 over exact canonical reconciliation UTF-8 bytes."""
    return sha256(
        publication_regeneration_export_reconciliation_canonical_bytes(
            reconciliation
        )
    ).hexdigest()


def persist_publication_regeneration_export_reconciliation(
    path: Path,
    reconciliation: PublicationRegenerationExportReconciliation,
) -> None:
    """Create or idempotently re-persist one explicit evidence sidecar.

    Validation and canonical byte derivation happen before filesystem mutation.
    Once exclusive creation succeeds, every later failure is ambiguous and the
    created artifact is retained without retry or repair.
    """
    contents = (
        publication_regeneration_export_reconciliation_canonical_bytes(
            reconciliation
        )
    )
    _validate_persistence_path(path)

    try:
        handle = path.open("xb")
    except FileExistsError:
        _persist_existing_reconciliation(path, contents)
        return
    except OSError as error:
        if error.errno == errno.EEXIST:
            _persist_existing_reconciliation(path, contents)
            return
        _raise_persistence("create")
    except Exception:
        _raise_persistence("create")

    _persist_new_reconciliation(handle, path, contents)


def load_publication_regeneration_export_reconciliation(
    path: Path,
) -> PublicationRegenerationExportReconciliation:
    """Strictly load one exact canonical evidence sidecar without mutation."""
    _validate_load_path(path)
    try:
        contents = path.read_bytes()
    except Exception:
        _raise_load("target")

    try:
        value = json.loads(
            contents.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
        reconciliation = _parse_reconciliation(value)
        if (
            _serialize_validated_reconciliation(reconciliation).encode("utf-8")
            != contents
        ):
            _raise_load("noncanonical")
        return reconciliation
    except PublicationRegenerationExportReconciliationEvidenceLoadError:
        raise
    except Exception:
        _raise_load("parse")


def _validated_reconciliation(
    reconciliation: object,
) -> PublicationRegenerationExportReconciliation:
    if type(reconciliation) is not PublicationRegenerationExportReconciliation:
        _raise_evidence("reconciliation_type")
    try:
        return PublicationRegenerationExportReconciliation(
            schema_version=reconciliation.schema_version,  # type: ignore[union-attr]
            regeneration_id=reconciliation.regeneration_id,  # type: ignore[union-attr]
            receipt_sha256=reconciliation.receipt_sha256,  # type: ignore[union-attr]
            status=reconciliation.status,  # type: ignore[union-attr]
            expected_business_output_sha256=reconciliation.expected_business_output_sha256,  # type: ignore[union-attr]
            expected_output_byte_length=reconciliation.expected_output_byte_length,  # type: ignore[union-attr]
            observed_business_output_sha256=reconciliation.observed_business_output_sha256,  # type: ignore[union-attr]
            observed_output_byte_length=reconciliation.observed_output_byte_length,  # type: ignore[union-attr]
        )
    except Exception:
        _raise_evidence("reconciliation")


def _serialize_validated_reconciliation(
    reconciliation: PublicationRegenerationExportReconciliation,
) -> str:
    try:
        return json.dumps(
            {
                "expected_business_output_sha256": (
                    reconciliation.expected_business_output_sha256
                ),
                "expected_output_byte_length": (
                    reconciliation.expected_output_byte_length
                ),
                "observed_business_output_sha256": (
                    reconciliation.observed_business_output_sha256
                ),
                "observed_output_byte_length": (
                    reconciliation.observed_output_byte_length
                ),
                "receipt_sha256": reconciliation.receipt_sha256,
                "regeneration_id": reconciliation.regeneration_id,
                "schema_version": reconciliation.schema_version,
                "status": reconciliation.status,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except Exception:
        _raise_evidence("serialization")


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
    except PublicationRegenerationExportReconciliationEvidencePersistenceError:
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
    except PublicationRegenerationExportReconciliationEvidenceLoadError:
        raise
    except OSError:
        _raise_load("target")
    except Exception:
        _raise_load("target")


def _persist_new_reconciliation(
    handle: object,
    path: Path,
    contents: bytes,
) -> None:
    try:
        with _evidence_handle_scope(handle) as active_handle:
            written = active_handle.write(contents)  # type: ignore[union-attr]
            if written != len(contents):
                raise OSError("short write")
            active_handle.flush()  # type: ignore[union-attr]
            os.fsync(active_handle.fileno())  # type: ignore[union-attr]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_evidence_directory(path.parent)
    except Exception:
        _raise_persistence("ambiguous")


@contextmanager
def _evidence_handle_scope(handle: object) -> Iterator[object]:
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


def _persist_existing_reconciliation(path: Path, contents: bytes) -> None:
    try:
        if path.is_dir() or path.is_symlink() or not path.is_file():
            _raise_persistence("target")
        existing = path.read_bytes()
    except PublicationRegenerationExportReconciliationEvidencePersistenceError:
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
        with _evidence_handle_scope(handle) as active_handle:
            os.fsync(active_handle.fileno())  # type: ignore[union-attr]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_evidence_directory(path.parent)
    except Exception:
        _raise_persistence("ambiguous")


def _fsync_evidence_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_reconciliation(
    value: object,
) -> PublicationRegenerationExportReconciliation:
    if type(value) is not dict or frozenset(value) != _EVIDENCE_KEYS:
        _raise_load("keys")
    try:
        return PublicationRegenerationExportReconciliation(
            schema_version=value["schema_version"],
            regeneration_id=value["regeneration_id"],
            receipt_sha256=value["receipt_sha256"],
            status=value["status"],
            expected_business_output_sha256=value[
                "expected_business_output_sha256"
            ],
            expected_output_byte_length=value["expected_output_byte_length"],
            observed_business_output_sha256=value[
                "observed_business_output_sha256"
            ],
            observed_output_byte_length=value["observed_output_byte_length"],
        )
    except Exception:
        _raise_load("reconciliation")


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result


class _DuplicateKeyError(ValueError):
    pass


def _reject_nonstandard_json_constant(constant: str) -> NoReturn:
    raise _NonStandardJSONConstantError


class _NonStandardJSONConstantError(ValueError):
    pass


def _raise_evidence(classification: str) -> NoReturn:
    raise PublicationRegenerationExportReconciliationEvidenceError(
        classification
    ) from None


def _raise_persistence(classification: str) -> NoReturn:
    raise PublicationRegenerationExportReconciliationEvidencePersistenceError(
        classification
    ) from None


def _raise_conflict() -> NoReturn:
    raise PublicationRegenerationExportReconciliationEvidenceConflictError(
        "conflict"
    ) from None


def _raise_load(classification: str) -> NoReturn:
    raise PublicationRegenerationExportReconciliationEvidenceLoadError(
        classification
    ) from None


__all__ = [
    "PublicationRegenerationExportReconciliationEvidenceConflictError",
    "PublicationRegenerationExportReconciliationEvidenceError",
    "PublicationRegenerationExportReconciliationEvidenceFailureDetail",
    "PublicationRegenerationExportReconciliationEvidenceLoadError",
    "PublicationRegenerationExportReconciliationEvidencePersistenceError",
    "load_publication_regeneration_export_reconciliation",
    "persist_publication_regeneration_export_reconciliation",
    "publication_regeneration_export_reconciliation_canonical_bytes",
    "publication_regeneration_export_reconciliation_digest",
    "serialize_publication_regeneration_export_reconciliation_canonical",
]
