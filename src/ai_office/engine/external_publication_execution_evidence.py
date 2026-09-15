"""Canonical durable evidence for one Phase 281 publication result.

Phase 282 persists an already-derived
:class:`ExternalPublicationExecutionResult` without executing publication or
reloading any predecessor artifact.  The persisted sidecar is the exact
result object itself: no wrapper, generated identity, timestamp, or additional
metadata is introduced.
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

from ai_office.engine.external_publication_execution import (
    ExternalPublicationExecutionResult,
)

_EVIDENCE_ERROR_MESSAGE = "external publication execution evidence is invalid"
_PERSISTENCE_ERROR_MESSAGE = (
    "external publication execution evidence persistence failed"
)
_LOAD_ERROR_MESSAGE = "external publication execution evidence could not be loaded"
_RESULT_SCHEMA_VERSION = "external-publication-execution-result.v1"
_RESULT_KEYS = frozenset(
    {
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
)
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class ExternalPublicationExecutionEvidenceFailureDetail:
    """Detail-safe classification for one evidence operation failure."""

    classification: str


class ExternalPublicationExecutionEvidenceError(ValueError):
    """Raised when an execution result is not exact canonical evidence."""

    def __init__(self, classification: str = "result") -> None:
        super().__init__(_EVIDENCE_ERROR_MESSAGE)
        self.detail = ExternalPublicationExecutionEvidenceFailureDetail(classification)


class ExternalPublicationExecutionEvidencePersistenceError(
    ExternalPublicationExecutionEvidenceError
):
    """Raised when evidence persistence cannot be proven durable."""

    def __init__(self, classification: str = "persistence") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = ExternalPublicationExecutionEvidenceFailureDetail(classification)


class ExternalPublicationExecutionEvidenceConflictError(
    ExternalPublicationExecutionEvidencePersistenceError
):
    """Raised when an existing target contains different bytes."""


class ExternalPublicationExecutionEvidenceLoadError(
    ExternalPublicationExecutionEvidenceError
):
    """Raised when a sidecar is not exact canonical execution evidence."""

    def __init__(self, classification: str = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = ExternalPublicationExecutionEvidenceFailureDetail(classification)


def serialize_external_publication_execution_result_canonical(
    result: ExternalPublicationExecutionResult,
) -> str:
    """Serialize one exact Phase 281 result as compact deterministic JSON."""
    validated = _validated_result(result)
    try:
        return json.dumps(
            {
                "business_output_sha256": validated.business_output_sha256,
                "output_byte_length": validated.output_byte_length,
                "publication_approval_sha256": (validated.publication_approval_sha256),
                "publication_attempt_claim_sha256": (
                    validated.publication_attempt_claim_sha256
                ),
                "publication_id": validated.publication_id,
                "publication_plan_sha256": validated.publication_plan_sha256,
                "publication_target_sha256": validated.publication_target_sha256,
                "provider": validated.provider,
                "regeneration_id": validated.regeneration_id,
                "schema_version": validated.schema_version,
                "status": validated.status,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except Exception:
        _raise_evidence("serialization")


def external_publication_execution_result_canonical_bytes(
    result: ExternalPublicationExecutionResult,
) -> bytes:
    """Return exact canonical execution-result JSON encoded as UTF-8 bytes."""
    try:
        return serialize_external_publication_execution_result_canonical(result).encode(
            "utf-8"
        )
    except ExternalPublicationExecutionEvidenceError:
        raise
    except UnicodeError:
        _raise_evidence("encoding")
    except Exception:
        _raise_evidence("encoding")


def external_publication_execution_result_digest(
    result: ExternalPublicationExecutionResult,
) -> str:
    """Return SHA-256 over exact canonical execution-result UTF-8 bytes."""
    try:
        return sha256(
            external_publication_execution_result_canonical_bytes(result)
        ).hexdigest()
    except ExternalPublicationExecutionEvidenceError:
        raise
    except Exception:
        _raise_evidence("encoding")


def persist_external_publication_execution_result(
    path: Path,
    result: ExternalPublicationExecutionResult,
) -> None:
    """Create or idempotently persist one exact execution-result sidecar.

    Result validation and canonical byte derivation happen before any target
    mutation.  Exclusive creation is the only new-file operation.  Once that
    creation succeeds, every later failure is ambiguous and the artifact is
    retained without cleanup, repair, replacement, or retry.
    """
    contents = external_publication_execution_result_canonical_bytes(result)
    _validate_persistence_path(path)

    try:
        handle = path.open("xb")
    except FileExistsError:
        _persist_existing(path, contents)
        return
    except OSError as error:
        if error.errno == errno.EEXIST:
            _persist_existing(path, contents)
            return
        _raise_persistence("create")
    except Exception:
        _raise_persistence("create")

    _persist_new(handle, path.parent, contents)


def load_external_publication_execution_result(
    path: Path,
) -> ExternalPublicationExecutionResult:
    """Read and strictly revalidate one immutable canonical result sidecar."""
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

    result = _parse_result(value)
    try:
        canonical = external_publication_execution_result_canonical_bytes(result)
    except ExternalPublicationExecutionEvidenceError:
        _raise_load("result")
    except Exception:
        _raise_load("result")
    if canonical != contents:
        _raise_load("noncanonical")
    return result


def _validated_result(
    result: object,
) -> ExternalPublicationExecutionResult:
    if type(result) is not ExternalPublicationExecutionResult:
        _raise_evidence("result_type")
    try:
        return ExternalPublicationExecutionResult(
            schema_version=result.schema_version,  # type: ignore[union-attr]
            regeneration_id=result.regeneration_id,  # type: ignore[union-attr]
            publication_attempt_claim_sha256=(
                result.publication_attempt_claim_sha256  # type: ignore[union-attr]
            ),
            publication_plan_sha256=(
                result.publication_plan_sha256  # type: ignore[union-attr]
            ),
            publication_approval_sha256=(
                result.publication_approval_sha256  # type: ignore[union-attr]
            ),
            business_output_sha256=(
                result.business_output_sha256  # type: ignore[union-attr]
            ),
            output_byte_length=result.output_byte_length,  # type: ignore[union-attr]
            provider=result.provider,  # type: ignore[union-attr]
            publication_target_sha256=(
                result.publication_target_sha256  # type: ignore[union-attr]
            ),
            publication_id=result.publication_id,  # type: ignore[union-attr]
            status=result.status,  # type: ignore[union-attr]
        )
    except Exception:
        _raise_evidence("result")


def _validate_persistence_path(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_persistence("path_type")
    try:
        if not path.parent.exists() or not path.parent.is_dir():  # type: ignore[union-attr]
            _raise_persistence("parent")
        if (
            path.is_symlink()  # type: ignore[union-attr]
            or path.is_dir()  # type: ignore[union-attr]
            or (path.exists() and not path.is_file())  # type: ignore[union-attr]
        ):
            _raise_persistence("target")
    except ExternalPublicationExecutionEvidencePersistenceError:
        raise
    except Exception:
        _raise_persistence("target")


def _validate_load_path(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_load("path_type")
    try:
        if (
            path.is_symlink()  # type: ignore[union-attr]
            or not path.exists()  # type: ignore[union-attr]
            or path.is_dir()  # type: ignore[union-attr]
            or not path.is_file()  # type: ignore[union-attr]
        ):
            _raise_load("target")
    except ExternalPublicationExecutionEvidenceLoadError:
        raise
    except Exception:
        _raise_load("target")


def _persist_new(handle: object, directory: Path, contents: bytes) -> None:
    try:
        with _evidence_handle_scope(handle) as active_handle:
            written = active_handle.write(contents)  # type: ignore[attr-defined]
            if type(written) is not int or written != len(contents):
                raise OSError("short evidence write")
            active_handle.flush()  # type: ignore[attr-defined]
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_evidence_directory(directory)
    except Exception:
        _raise_persistence("ambiguous")


def _persist_existing(path: Path, contents: bytes) -> None:
    try:
        if (
            path.is_symlink()
            or not path.exists()
            or path.is_dir()
            or not path.is_file()
        ):
            _raise_persistence("target")
        existing = path.read_bytes()
        if type(existing) is not bytes:
            _raise_persistence("target")
    except ExternalPublicationExecutionEvidencePersistenceError:
        raise
    except Exception:
        _raise_persistence("target")

    if existing != contents:
        _raise_conflict()

    try:
        handle = path.open("rb")
    except Exception:
        _raise_persistence("ambiguous")

    try:
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
            raise OSError("evidence handle cannot close")
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


def _parse_result(value: object) -> ExternalPublicationExecutionResult:
    if type(value) is not dict or frozenset(value) != _RESULT_KEYS:
        _raise_load("keys")
    try:
        return ExternalPublicationExecutionResult(
            schema_version=value["schema_version"],
            regeneration_id=value["regeneration_id"],
            publication_attempt_claim_sha256=(
                value["publication_attempt_claim_sha256"]
            ),
            publication_plan_sha256=value["publication_plan_sha256"],
            publication_approval_sha256=value["publication_approval_sha256"],
            business_output_sha256=value["business_output_sha256"],
            output_byte_length=value["output_byte_length"],
            provider=value["provider"],
            publication_target_sha256=value["publication_target_sha256"],
            publication_id=value["publication_id"],
            status=value["status"],
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
    raise ExternalPublicationExecutionEvidenceError(classification) from None


def _raise_persistence(classification: str) -> NoReturn:
    raise ExternalPublicationExecutionEvidencePersistenceError(classification) from None


def _raise_conflict() -> NoReturn:
    raise ExternalPublicationExecutionEvidenceConflictError("conflict") from None


def _raise_load(classification: str) -> NoReturn:
    raise ExternalPublicationExecutionEvidenceLoadError(classification) from None


__all__ = [
    "ExternalPublicationExecutionEvidenceConflictError",
    "ExternalPublicationExecutionEvidenceError",
    "ExternalPublicationExecutionEvidenceFailureDetail",
    "ExternalPublicationExecutionEvidenceLoadError",
    "ExternalPublicationExecutionEvidencePersistenceError",
    "external_publication_execution_result_canonical_bytes",
    "external_publication_execution_result_digest",
    "load_external_publication_execution_result",
    "persist_external_publication_execution_result",
    "serialize_external_publication_execution_result_canonical",
]
