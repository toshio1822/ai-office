"""Canonical durable evidence for one Phase 283 reconciliation result.

Phase 284 persists only an already-derived exact
:class:`ExternalPublicationExecutionReconciliation`.  It does not rerun
reconciliation, load either predecessor sidecar, inspect provider state, or
perform any external execution.
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

from ai_office.engine.external_publication_execution_reconciliation import (
    ExternalPublicationExecutionReconciliation,
)

_EVIDENCE_ERROR_MESSAGE = (
    "external publication execution reconciliation evidence is invalid"
)
_PERSISTENCE_ERROR_MESSAGE = (
    "external publication execution reconciliation evidence persistence failed"
)
_LOAD_ERROR_MESSAGE = (
    "external publication execution reconciliation evidence could not be loaded"
)
_EVIDENCE_SCHEMA_VERSION = "external-publication-execution-reconciliation.v1"
_EVIDENCE_KEYS = frozenset(
    {
        "schema_version",
        "claim_sha256",
        "execution_evidence_sha256",
        "status",
        "mismatched_fields",
    }
)
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class ExternalPublicationExecutionReconciliationEvidenceFailureDetail:
    """Detail-safe classification for one evidence operation failure."""

    classification: str


class ExternalPublicationExecutionReconciliationEvidenceError(ValueError):
    """Raised when a reconciliation is not exact canonical evidence."""

    def __init__(self, classification: str = "reconciliation") -> None:
        super().__init__(_EVIDENCE_ERROR_MESSAGE)
        self.detail = ExternalPublicationExecutionReconciliationEvidenceFailureDetail(
            classification
        )


class ExternalPublicationExecutionReconciliationEvidencePersistenceError(
    ExternalPublicationExecutionReconciliationEvidenceError
):
    """Raised when evidence persistence cannot be proven durable."""

    def __init__(self, classification: str = "persistence") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = ExternalPublicationExecutionReconciliationEvidenceFailureDetail(
            classification
        )


class ExternalPublicationExecutionReconciliationEvidenceConflictError(
    ExternalPublicationExecutionReconciliationEvidencePersistenceError
):
    """Raised when an existing target contains different bytes."""


class ExternalPublicationExecutionReconciliationEvidenceLoadError(
    ExternalPublicationExecutionReconciliationEvidenceError
):
    """Raised when a target is not exact canonical reconciliation evidence."""

    def __init__(self, classification: str = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = ExternalPublicationExecutionReconciliationEvidenceFailureDetail(
            classification
        )


def serialize_external_publication_execution_reconciliation_canonical(
    reconciliation: ExternalPublicationExecutionReconciliation,
) -> str:
    """Serialize one exact Phase 283 result as compact deterministic JSON."""
    validated = _validated_reconciliation(reconciliation)
    return _serialize_validated_reconciliation(validated)


def external_publication_execution_reconciliation_canonical_bytes(
    reconciliation: ExternalPublicationExecutionReconciliation,
) -> bytes:
    """Return exact canonical reconciliation JSON encoded as UTF-8 bytes."""
    try:
        return serialize_external_publication_execution_reconciliation_canonical(
            reconciliation
        ).encode("utf-8")
    except ExternalPublicationExecutionReconciliationEvidenceError:
        raise
    except UnicodeError:
        _raise_evidence("encoding")
    except Exception:
        _raise_evidence("encoding")


def external_publication_execution_reconciliation_digest(
    reconciliation: ExternalPublicationExecutionReconciliation,
) -> str:
    """Return SHA-256 over exact canonical reconciliation UTF-8 bytes."""
    try:
        return sha256(
            external_publication_execution_reconciliation_canonical_bytes(
                reconciliation
            )
        ).hexdigest()
    except ExternalPublicationExecutionReconciliationEvidenceError:
        raise
    except Exception:
        _raise_evidence("encoding")


def persist_external_publication_execution_reconciliation(
    path: Path,
    reconciliation: ExternalPublicationExecutionReconciliation,
) -> None:
    """Create or idempotently persist one explicit reconciliation sidecar.

    Validation and canonical byte derivation happen before filesystem mutation.
    Once exclusive creation succeeds, every later failure is ambiguous and the
    created artifact is retained without retry, repair, replacement, or
    deletion.
    """
    contents = external_publication_execution_reconciliation_canonical_bytes(
        reconciliation
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

    _persist_new_reconciliation(handle, path.parent, contents)


def load_external_publication_execution_reconciliation(
    path: Path,
) -> ExternalPublicationExecutionReconciliation:
    """Strictly load one exact canonical reconciliation sidecar read-only."""
    _validate_load_path(path)
    try:
        contents = path.read_bytes()
    except Exception:
        _raise_load("target")
    if type(contents) is not bytes:
        _raise_load("target")

    try:
        value = json.loads(
            contents.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKeyError,
        _NonStandardJSONConstantError,
    ):
        _raise_load("parse")
    except Exception:
        _raise_load("parse")

    reconciliation = _parse_reconciliation(value)
    try:
        canonical = _serialize_validated_reconciliation(reconciliation).encode("utf-8")
    except Exception:
        _raise_load("result")
    if canonical != contents:
        _raise_load("noncanonical")
    return reconciliation


def _validated_reconciliation(
    reconciliation: object,
) -> ExternalPublicationExecutionReconciliation:
    if type(reconciliation) is not ExternalPublicationExecutionReconciliation:
        _raise_evidence("reconciliation_type")
    try:
        # Reconstruct the exact Phase 283 model instead of trusting a forged
        # exact instance's stored attributes.
        return ExternalPublicationExecutionReconciliation(
            schema_version=reconciliation.schema_version,  # type: ignore[union-attr]
            claim_sha256=reconciliation.claim_sha256,  # type: ignore[union-attr]
            execution_evidence_sha256=(
                reconciliation.execution_evidence_sha256  # type: ignore[union-attr]
            ),
            status=reconciliation.status,  # type: ignore[union-attr]
            mismatched_fields=reconciliation.mismatched_fields,  # type: ignore[union-attr]
        )
    except Exception:
        _raise_evidence("reconciliation")


def _serialize_validated_reconciliation(
    reconciliation: ExternalPublicationExecutionReconciliation,
) -> str:
    try:
        return json.dumps(
            {
                "claim_sha256": reconciliation.claim_sha256,
                "execution_evidence_sha256": (reconciliation.execution_evidence_sha256),
                "mismatched_fields": list(reconciliation.mismatched_fields),
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
        if _target_is_not_regular(path):  # type: ignore[arg-type]
            _raise_persistence("target")
    except ExternalPublicationExecutionReconciliationEvidencePersistenceError:
        raise
    except Exception:
        _raise_persistence("target")


def _validate_load_path(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_load("path_type")
    try:
        if _target_is_not_regular(path, missing_is_invalid=True):  # type: ignore[arg-type]
            _raise_load("target")
    except ExternalPublicationExecutionReconciliationEvidenceLoadError:
        raise
    except Exception:
        _raise_load("target")


def _target_is_not_regular(path: Path, *, missing_is_invalid: bool = False) -> bool:
    if path.is_symlink() or path.is_dir():
        return True
    if not path.exists():
        return missing_is_invalid
    return not path.is_file()


def _persist_new_reconciliation(
    handle: object,
    directory: Path,
    contents: bytes,
) -> None:
    try:
        with _evidence_handle_scope(handle) as active_handle:
            written = active_handle.write(contents)  # type: ignore[attr-defined]
            if type(written) is not int or written != len(contents):
                raise OSError("short reconciliation evidence write")
            active_handle.flush()  # type: ignore[attr-defined]
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_evidence_directory(directory)
    except Exception:
        _raise_persistence("ambiguous")


def _persist_existing_reconciliation(path: Path, contents: bytes) -> None:
    try:
        if _target_is_not_regular(path, missing_is_invalid=True):
            _raise_persistence("target")
        existing = path.read_bytes()
    except ExternalPublicationExecutionReconciliationEvidencePersistenceError:
        raise
    except Exception:
        _raise_persistence("target")
    if type(existing) is not bytes:
        _raise_persistence("target")

    if existing != contents:
        _raise_conflict()

    try:
        handle = path.open("rb")
        with _evidence_handle_scope(handle) as active_handle:
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
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
        close = getattr(handle, "close", None)
        if not callable(close):
            raise OSError("reconciliation evidence handle cannot close")
        close()


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
) -> ExternalPublicationExecutionReconciliation:
    if type(value) is not dict or frozenset(value) != _EVIDENCE_KEYS:
        _raise_load("keys")
    if type(value["mismatched_fields"]) is not list:
        _raise_load("result")
    try:
        return ExternalPublicationExecutionReconciliation(
            schema_version=value["schema_version"],
            claim_sha256=value["claim_sha256"],
            execution_evidence_sha256=value["execution_evidence_sha256"],
            status=value["status"],
            mismatched_fields=tuple(value["mismatched_fields"]),
        )
    except Exception:
        _raise_load("result")


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


class _NonStandardJSONConstantError(ValueError):
    pass


def _reject_nonstandard_json_constant(value: str) -> NoReturn:
    del value
    raise _NonStandardJSONConstantError


def _raise_evidence(classification: str) -> NoReturn:
    raise ExternalPublicationExecutionReconciliationEvidenceError(
        classification
    ) from None


def _raise_persistence(classification: str) -> NoReturn:
    raise ExternalPublicationExecutionReconciliationEvidencePersistenceError(
        classification
    ) from None


def _raise_conflict() -> NoReturn:
    raise ExternalPublicationExecutionReconciliationEvidenceConflictError(
        "conflict"
    ) from None


def _raise_load(classification: str) -> NoReturn:
    raise ExternalPublicationExecutionReconciliationEvidenceLoadError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationExecutionReconciliationEvidenceConflictError",
    "ExternalPublicationExecutionReconciliationEvidenceError",
    "ExternalPublicationExecutionReconciliationEvidenceFailureDetail",
    "ExternalPublicationExecutionReconciliationEvidenceLoadError",
    "ExternalPublicationExecutionReconciliationEvidencePersistenceError",
    "external_publication_execution_reconciliation_canonical_bytes",
    "external_publication_execution_reconciliation_digest",
    "load_external_publication_execution_reconciliation",
    "persist_external_publication_execution_reconciliation",
    "serialize_external_publication_execution_reconciliation_canonical",
]
