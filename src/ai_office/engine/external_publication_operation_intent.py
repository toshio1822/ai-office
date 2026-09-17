"""Canonical durable intent for one explicit external-publication operation.

Phase 289 records only the operation explicitly selected by a caller and the
exact approval/plan lineage to which that choice was bound.  It does not run
the runtime dispatcher, publish, inspect predecessor sidecars, or infer a
route from sidecar or prior state.
"""

from __future__ import annotations

import errno
import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn

from .external_publication import (
    ExternalPublicationApproval,
    ExternalPublicationError,
    external_publication_approval_digest,
)

_INTENT_ERROR_MESSAGE = "external publication operation intent is invalid"
_PERSISTENCE_ERROR_MESSAGE = "external publication operation intent persistence failed"
_LOAD_ERROR_MESSAGE = "external publication operation intent could not be loaded"
_INTENT_SCHEMA_VERSION = "external-publication-operation-intent.v1"
_INTENT_KEYS = frozenset(
    {
        "operation",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "schema_version",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_MAX_INTENT_BYTES = 4096


@dataclass(frozen=True)
class ExternalPublicationOperationIntentFailureDetail:
    """Detail-safe classification for one intent operation failure."""

    classification: str


class ExternalPublicationOperationIntentError(ValueError):
    """Raised when an operation intent is not exact and safe."""

    def __init__(self, classification: str = "contract") -> None:
        super().__init__(_INTENT_ERROR_MESSAGE)
        self.detail = ExternalPublicationOperationIntentFailureDetail(classification)


class ExternalPublicationOperationIntentPersistenceError(
    ExternalPublicationOperationIntentError
):
    """Raised when intent persistence cannot be proven durable."""

    def __init__(self, classification: str = "persistence") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = ExternalPublicationOperationIntentFailureDetail(classification)


class ExternalPublicationOperationIntentConflictError(
    ExternalPublicationOperationIntentPersistenceError
):
    """Raised when an existing target contains different canonical bytes."""


class ExternalPublicationOperationIntentLoadError(
    ExternalPublicationOperationIntentError
):
    """Raised when a sidecar is not an exact canonical intent record."""

    def __init__(self, classification: str = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = ExternalPublicationOperationIntentFailureDetail(classification)


@dataclass(frozen=True)
class ExternalPublicationOperationIntent:
    """Immutable, secret-free record of one explicit operation selection."""

    schema_version: Literal["external-publication-operation-intent.v1"]
    publication_approval_sha256: str
    publication_plan_sha256: str
    operation: Literal["fresh", "resume"]

    def __post_init__(self) -> None:
        _validate_intent(self)

    @property
    def digest(self) -> str:
        """Return the SHA-256 identity of canonical intent JSON."""
        return external_publication_operation_intent_digest(self)


ApprovalDigestFunction = Callable[[ExternalPublicationApproval], object]


def build_external_publication_operation_intent(
    approval: ExternalPublicationApproval,
    *,
    operation: Literal["fresh", "resume"],
    approval_digest_function: ApprovalDigestFunction = (
        external_publication_approval_digest
    ),
) -> ExternalPublicationOperationIntent:
    """Build one durable intent from one exact approval and explicit operation.

    The approval digest helper is the existing authoritative approval contract:
    it validates the exact approval without reconstructing a publication plan.
    The helper is called once with the caller's exact approval object.  The
    returned approval digest and the plan digest already carried by that exact
    approval are then bound into one immutable record.
    """
    if type(approval) is not ExternalPublicationApproval:
        _raise_intent("approval_type")
    if type(operation) is not str or operation not in {"fresh", "resume"}:
        _raise_intent("operation")
    if not callable(approval_digest_function):
        _raise_intent("configuration")

    try:
        approval_sha256 = approval_digest_function(approval)
    except ExternalPublicationError:
        raise
    except Exception:
        _raise_intent("dependency_error")

    if not _is_sha256(approval_sha256):
        _raise_intent("approval_digest")
    plan_sha256 = approval.publication_plan_sha256
    if not _is_sha256(plan_sha256):
        _raise_intent("plan_digest")

    try:
        return ExternalPublicationOperationIntent(
            schema_version=_INTENT_SCHEMA_VERSION,
            publication_approval_sha256=approval_sha256,
            publication_plan_sha256=plan_sha256,
            operation=operation,
        )
    except ExternalPublicationOperationIntentError:
        raise
    except Exception:
        _raise_intent("intent")


def serialize_external_publication_operation_intent_canonical(
    intent: ExternalPublicationOperationIntent,
) -> str:
    """Serialize one exact intent as compact deterministic JSON."""
    _validate_intent(intent)
    try:
        return json.dumps(
            {
                "operation": intent.operation,
                "publication_approval_sha256": intent.publication_approval_sha256,
                "publication_plan_sha256": intent.publication_plan_sha256,
                "schema_version": intent.schema_version,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except Exception:
        _raise_intent("serialization")


def external_publication_operation_intent_canonical_bytes(
    intent: ExternalPublicationOperationIntent,
) -> bytes:
    """Return exact canonical intent JSON encoded as UTF-8 bytes."""
    try:
        return serialize_external_publication_operation_intent_canonical(intent).encode(
            "utf-8"
        )
    except ExternalPublicationOperationIntentError:
        raise
    except UnicodeError:
        _raise_intent("encoding")
    except Exception:
        _raise_intent("encoding")


def external_publication_operation_intent_digest(
    intent: ExternalPublicationOperationIntent,
) -> str:
    """Return SHA-256 over exact canonical intent UTF-8 bytes."""
    return sha256(
        external_publication_operation_intent_canonical_bytes(intent)
    ).hexdigest()


def persist_external_publication_operation_intent(
    path: Path,
    intent: ExternalPublicationOperationIntent,
) -> None:
    """Create or idempotently persist one exact immutable intent sidecar.

    Canonical bytes and the target contract are derived before creation.  Once
    exclusive creation succeeds, all write, file-sync, close, and directory-sync
    failures are ambiguous; the created artifact is retained without cleanup,
    overwrite, repair, or another attempt.
    """
    contents = external_publication_operation_intent_canonical_bytes(intent)
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


def load_external_publication_operation_intent(
    path: Path,
) -> ExternalPublicationOperationIntent:
    """Read and strictly revalidate one canonical immutable intent sidecar."""
    _validate_load_path(path)
    try:
        with path.open("rb") as handle:
            contents = handle.read(_MAX_INTENT_BYTES + 1)
    except Exception:
        _raise_load("target")
    if type(contents) is not bytes:
        _raise_load("target")
    if len(contents) > _MAX_INTENT_BYTES:
        _raise_load("size")

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

    intent = _parse_intent(value)
    try:
        canonical = external_publication_operation_intent_canonical_bytes(intent)
    except ExternalPublicationOperationIntentError:
        _raise_load("intent")
    except Exception:
        _raise_load("intent")
    if canonical != contents:
        _raise_load("noncanonical")
    return intent


def _validate_intent(intent: object) -> None:
    if type(intent) is not ExternalPublicationOperationIntent:
        _raise_intent("intent_type")
    try:
        if (
            type(intent.schema_version) is not str
            or intent.schema_version != _INTENT_SCHEMA_VERSION
        ):
            _raise_intent("schema_version")
        if not _is_sha256(intent.publication_approval_sha256):
            _raise_intent("approval_digest")
        if not _is_sha256(intent.publication_plan_sha256):
            _raise_intent("plan_digest")
        if type(intent.operation) is not str or intent.operation not in {
            "fresh",
            "resume",
        }:
            _raise_intent("operation")
    except ExternalPublicationOperationIntentError:
        raise
    except Exception:
        _raise_intent("intent")


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _validate_persistence_path(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_persistence("path_type")
    try:
        if not path.parent.exists() or not path.parent.is_dir():  # type: ignore[union-attr]
            _raise_persistence("parent")
    except ExternalPublicationOperationIntentPersistenceError:
        raise
    except Exception:
        _raise_persistence("parent")

    try:
        if (
            path.is_symlink()  # type: ignore[union-attr]
            or path.is_dir()  # type: ignore[union-attr]
            or (path.exists() and not path.is_file())  # type: ignore[union-attr]
        ):
            _raise_persistence("target")
    except ExternalPublicationOperationIntentPersistenceError:
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
    except ExternalPublicationOperationIntentLoadError:
        raise
    except Exception:
        _raise_load("target")


def _persist_new(handle: object, directory: Path, contents: bytes) -> None:
    try:
        with _intent_handle_scope(handle) as active_handle:
            written = active_handle.write(contents)  # type: ignore[attr-defined]
            if type(written) is not int or written != len(contents):
                raise OSError("short intent write")
            active_handle.flush()  # type: ignore[attr-defined]
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_intent_directory(directory)
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
    except ExternalPublicationOperationIntentPersistenceError:
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
        with _intent_handle_scope(handle) as active_handle:
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_intent_directory(path.parent)
    except Exception:
        _raise_persistence("ambiguous")


@contextmanager
def _intent_handle_scope(handle: object) -> Iterator[object]:
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
            raise OSError("intent handle cannot close")
        close()


def _fsync_intent_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_intent(value: object) -> ExternalPublicationOperationIntent:
    if type(value) is not dict or frozenset(value) != _INTENT_KEYS:
        _raise_load("keys")
    try:
        return ExternalPublicationOperationIntent(
            schema_version=value["schema_version"],
            publication_approval_sha256=value["publication_approval_sha256"],
            publication_plan_sha256=value["publication_plan_sha256"],
            operation=value["operation"],
        )
    except Exception:
        _raise_load("intent")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
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


def _raise_intent(classification: str) -> NoReturn:
    raise ExternalPublicationOperationIntentError(classification) from None


def _raise_persistence(classification: str) -> NoReturn:
    raise ExternalPublicationOperationIntentPersistenceError(classification) from None


def _raise_conflict() -> NoReturn:
    raise ExternalPublicationOperationIntentConflictError("conflict") from None


def _raise_load(classification: str) -> NoReturn:
    raise ExternalPublicationOperationIntentLoadError(classification) from None


__all__ = [
    "ExternalPublicationOperationIntent",
    "ExternalPublicationOperationIntentConflictError",
    "ExternalPublicationOperationIntentError",
    "ExternalPublicationOperationIntentFailureDetail",
    "ExternalPublicationOperationIntentLoadError",
    "ExternalPublicationOperationIntentPersistenceError",
    "build_external_publication_operation_intent",
    "external_publication_operation_intent_canonical_bytes",
    "external_publication_operation_intent_digest",
    "load_external_publication_operation_intent",
    "persist_external_publication_operation_intent",
    "serialize_external_publication_operation_intent_canonical",
]
