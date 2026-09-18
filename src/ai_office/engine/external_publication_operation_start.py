"""Crash-safe durable start acquisition for one explicit publication intent.

Phase 290 adds an append-only higher-level lifecycle fence on top of the
Phase 289 durable operation intent.  It records neither execution nor provider
state: it only loads one exact intent, binds its digest and lineage into one
canonical start marker, and acquires that marker exclusively.
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

from .external_publication_operation_intent import (
    ExternalPublicationOperationIntent,
    ExternalPublicationOperationIntentError,
    external_publication_operation_intent_digest,
    load_external_publication_operation_intent,
)

_START_ERROR_MESSAGE = "external publication operation start is invalid"
_PERSISTENCE_ERROR_MESSAGE = "external publication operation start persistence failed"
_LOAD_ERROR_MESSAGE = "external publication operation start could not be loaded"
_START_SCHEMA_VERSION = "external-publication-operation-start.v1"
_START_KEYS = frozenset(
    {
        "operation",
        "operation_intent_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "schema_version",
        "state",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_MAX_START_BYTES = 4096
_INTENT_SCHEMA_VERSION = "external-publication-operation-intent.v1"


@dataclass(frozen=True)
class ExternalPublicationOperationStartFailureDetail:
    """Detail-safe classification for one start operation failure."""

    classification: str


class ExternalPublicationOperationStartError(ValueError):
    """Raised when a start marker or acquisition input is not exact."""

    def __init__(self, classification: str = "contract") -> None:
        super().__init__(_START_ERROR_MESSAGE)
        self.detail = ExternalPublicationOperationStartFailureDetail(classification)


class ExternalPublicationOperationStartPersistenceError(
    ExternalPublicationOperationStartError
):
    """Raised when start-marker durability cannot be proven."""

    def __init__(self, classification: str = "persistence") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = ExternalPublicationOperationStartFailureDetail(classification)


class ExternalPublicationOperationStartConflictError(
    ExternalPublicationOperationStartPersistenceError
):
    """Raised when an occupied start target is not the exact marker."""


class ExternalPublicationOperationStartLoadError(
    ExternalPublicationOperationStartError
):
    """Raised when a start marker is not an exact canonical record."""

    def __init__(self, classification: str = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = ExternalPublicationOperationStartFailureDetail(classification)


@dataclass(frozen=True)
class ExternalPublicationOperationStart:
    """Immutable, secret-free evidence that the higher-level start fence exists."""

    schema_version: Literal["external-publication-operation-start.v1"]
    operation_intent_sha256: str
    publication_approval_sha256: str
    publication_plan_sha256: str
    operation: Literal["fresh", "resume"]
    state: Literal["started"]

    def __post_init__(self) -> None:
        _validate_start(self)


@dataclass(frozen=True)
class ExternalPublicationOperationStartAcquisition:
    """The non-persisted result of one exclusive start-marker acquisition."""

    status: Literal["acquired", "already_acquired"]
    start: ExternalPublicationOperationStart

    def __post_init__(self) -> None:
        if type(self) is not ExternalPublicationOperationStartAcquisition:
            _raise_start("acquisition_type")
        if type(self.status) is not str or self.status not in {
            "acquired",
            "already_acquired",
        }:
            _raise_start("status")
        if type(self.start) is not ExternalPublicationOperationStart:
            _raise_start("start_type")
        _validate_start(self.start)


IntentLoader = Callable[[Path], object]
IntentDigestFunction = Callable[[ExternalPublicationOperationIntent], object]


def serialize_external_publication_operation_start_canonical(
    start: ExternalPublicationOperationStart,
) -> str:
    """Serialize one exact start marker as compact deterministic JSON."""
    _validate_start(start)
    try:
        return json.dumps(
            {
                "operation": start.operation,
                "operation_intent_sha256": start.operation_intent_sha256,
                "publication_approval_sha256": start.publication_approval_sha256,
                "publication_plan_sha256": start.publication_plan_sha256,
                "schema_version": start.schema_version,
                "state": start.state,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except Exception:
        _raise_start("serialization")


def external_publication_operation_start_canonical_bytes(
    start: ExternalPublicationOperationStart,
) -> bytes:
    """Return exact canonical start JSON encoded as UTF-8 bytes."""
    try:
        return serialize_external_publication_operation_start_canonical(start).encode(
            "utf-8"
        )
    except ExternalPublicationOperationStartError:
        raise
    except UnicodeError:
        _raise_start("encoding")
    except Exception:
        _raise_start("encoding")


def external_publication_operation_start_digest(
    start: ExternalPublicationOperationStart,
) -> str:
    """Return SHA-256 over exact canonical start UTF-8 bytes."""
    return sha256(
        external_publication_operation_start_canonical_bytes(start)
    ).hexdigest()


def load_external_publication_operation_start(
    path: Path,
) -> ExternalPublicationOperationStart:
    """Read and strictly revalidate one immutable canonical start marker."""
    _validate_load_path(path)
    try:
        with path.open("rb") as handle:
            contents = handle.read(_MAX_START_BYTES + 1)
    except Exception:
        _raise_load("target")
    if type(contents) is not bytes:
        _raise_load("target")
    if len(contents) > _MAX_START_BYTES:
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

    start = _parse_start(value)
    try:
        canonical = external_publication_operation_start_canonical_bytes(start)
    except ExternalPublicationOperationStartError:
        _raise_load("start")
    except Exception:
        _raise_load("start")
    if canonical != contents:
        _raise_load("noncanonical")
    return start


def acquire_external_publication_operation_start(
    *,
    intent_path: Path,
    start_path: Path,
    intent_loader: IntentLoader = load_external_publication_operation_intent,
    intent_digest_function: IntentDigestFunction = (
        external_publication_operation_intent_digest
    ),
) -> ExternalPublicationOperationStartAcquisition:
    """Exclusively acquire the immutable start marker for one exact intent.

    ``acquired`` is returned only by the invocation that created and durably
    committed the marker.  An identical marker that already existed is always
    reported as ``already_acquired``; it is never a fresh execution
    authorization.  This function never dispatches a publication operation.
    """
    _validate_acquisition_configuration(
        intent_path, start_path, intent_loader, intent_digest_function
    )

    try:
        intent = intent_loader(intent_path)
    except ExternalPublicationOperationIntentError:
        raise
    except Exception:
        _raise_start("dependency_error")
    if type(intent) is not ExternalPublicationOperationIntent:
        _raise_start("intent_contract")

    try:
        intent_digest = intent_digest_function(intent)
    except ExternalPublicationOperationIntentError:
        raise
    except Exception:
        _raise_start("dependency_error")
    if not _is_sha256(intent_digest):
        _raise_start("intent_digest")
    _validate_loaded_intent(intent)

    try:
        start = ExternalPublicationOperationStart(
            schema_version=_START_SCHEMA_VERSION,
            operation_intent_sha256=intent_digest,
            publication_approval_sha256=intent.publication_approval_sha256,
            publication_plan_sha256=intent.publication_plan_sha256,
            operation=intent.operation,
            state="started",
        )
        contents = external_publication_operation_start_canonical_bytes(start)
    except ExternalPublicationOperationStartError:
        _raise_start("intent_contract")
    except Exception:
        _raise_start("intent_contract")

    status = _acquire_start_marker(start_path, contents)
    try:
        return ExternalPublicationOperationStartAcquisition(status=status, start=start)
    except ExternalPublicationOperationStartError:
        _raise_start("acquisition")


def _validate_acquisition_configuration(
    intent_path: object,
    start_path: object,
    intent_loader: object,
    intent_digest_function: object,
) -> None:
    if type(intent_path) is not _PATH_TYPE or type(start_path) is not _PATH_TYPE:
        _raise_start("path_type")
    if not callable(intent_loader) or not callable(intent_digest_function):
        _raise_start("configuration")


def _validate_loaded_intent(intent: ExternalPublicationOperationIntent) -> None:
    try:
        if (
            type(intent.schema_version) is not str
            or intent.schema_version != _INTENT_SCHEMA_VERSION
            or not _is_sha256(intent.publication_approval_sha256)
            or not _is_sha256(intent.publication_plan_sha256)
            or type(intent.operation) is not str
            or intent.operation not in {"fresh", "resume"}
        ):
            _raise_start("intent_contract")
    except ExternalPublicationOperationStartError:
        raise
    except Exception:
        _raise_start("intent_contract")


def _validate_start(start: object) -> None:
    if type(start) is not ExternalPublicationOperationStart:
        _raise_start("start_type")
    try:
        if (
            type(start.schema_version) is not str  # type: ignore[union-attr]
            or start.schema_version != _START_SCHEMA_VERSION  # type: ignore[union-attr]
        ):
            _raise_start("schema_version")
        if not _is_sha256(start.operation_intent_sha256):  # type: ignore[union-attr]
            _raise_start("intent_digest")
        if not _is_sha256(start.publication_approval_sha256):  # type: ignore[union-attr]
            _raise_start("approval_digest")
        if not _is_sha256(start.publication_plan_sha256):  # type: ignore[union-attr]
            _raise_start("plan_digest")
        if (
            type(start.operation) is not str  # type: ignore[union-attr]
            or start.operation not in {"fresh", "resume"}  # type: ignore[union-attr]
        ):
            _raise_start("operation")
        if (
            type(start.state) is not str  # type: ignore[union-attr]
            or start.state != "started"  # type: ignore[union-attr]
        ):
            _raise_start("state")
    except ExternalPublicationOperationStartError:
        raise
    except Exception:
        _raise_start("start")


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _validate_persistence_target(path: object) -> None:
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
    except ExternalPublicationOperationStartPersistenceError:
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
    except ExternalPublicationOperationStartLoadError:
        raise
    except Exception:
        _raise_load("target")


def _acquire_start_marker(
    path: Path, contents: bytes
) -> Literal["acquired", "already_acquired"]:
    _validate_persistence_target(path)
    try:
        handle = path.open("xb")
    except FileExistsError:
        return _verify_existing_start(path, contents)
    except OSError as error:
        if error.errno == errno.EEXIST:
            return _verify_existing_start(path, contents)
        _raise_persistence("create")
    except Exception:
        _raise_persistence("create")

    _persist_new_start(handle, path.parent, contents)
    return "acquired"


def _persist_new_start(handle: object, directory: Path, contents: bytes) -> None:
    try:
        with _start_handle_scope(handle) as active_handle:
            written = active_handle.write(contents)  # type: ignore[attr-defined]
            if type(written) is not int or written != len(contents):
                raise OSError("short start write")
            active_handle.flush()  # type: ignore[attr-defined]
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_operation_start_directory(directory)
    except Exception:
        _raise_persistence("ambiguous")


def _verify_existing_start(path: Path, contents: bytes) -> Literal["already_acquired"]:
    try:
        if (
            path.is_symlink()
            or not path.exists()
            or path.is_dir()
            or not path.is_file()
        ):
            _raise_persistence("target")
        with path.open("rb") as handle:
            existing = handle.read(_MAX_START_BYTES + 1)
        if type(existing) is not bytes:
            _raise_persistence("target")
    except ExternalPublicationOperationStartPersistenceError:
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
        with _start_handle_scope(handle) as active_handle:
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")
    try:
        _fsync_operation_start_directory(path.parent)
    except Exception:
        _raise_persistence("ambiguous")
    return "already_acquired"


@contextmanager
def _start_handle_scope(handle: object) -> Iterator[object]:
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
            raise OSError("start handle cannot close")
        close()


def _fsync_operation_start_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_start(value: object) -> ExternalPublicationOperationStart:
    if type(value) is not dict or frozenset(value) != _START_KEYS:
        _raise_load("keys")
    try:
        return ExternalPublicationOperationStart(
            schema_version=value["schema_version"],
            operation_intent_sha256=value["operation_intent_sha256"],
            publication_approval_sha256=value["publication_approval_sha256"],
            publication_plan_sha256=value["publication_plan_sha256"],
            operation=value["operation"],
            state=value["state"],
        )
    except Exception:
        _raise_load("start")


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


def _raise_start(classification: str) -> NoReturn:
    raise ExternalPublicationOperationStartError(classification) from None


def _raise_persistence(classification: str) -> NoReturn:
    raise ExternalPublicationOperationStartPersistenceError(classification) from None


def _raise_conflict() -> NoReturn:
    raise ExternalPublicationOperationStartConflictError("conflict") from None


def _raise_load(classification: str) -> NoReturn:
    raise ExternalPublicationOperationStartLoadError(classification) from None


__all__ = [
    "ExternalPublicationOperationStart",
    "ExternalPublicationOperationStartAcquisition",
    "ExternalPublicationOperationStartConflictError",
    "ExternalPublicationOperationStartError",
    "ExternalPublicationOperationStartFailureDetail",
    "ExternalPublicationOperationStartLoadError",
    "ExternalPublicationOperationStartPersistenceError",
    "acquire_external_publication_operation_start",
    "external_publication_operation_start_canonical_bytes",
    "external_publication_operation_start_digest",
    "load_external_publication_operation_start",
    "serialize_external_publication_operation_start_canonical",
]
