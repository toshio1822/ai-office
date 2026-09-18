"""Crash-safe durable provenance binding for a recovery-authorized resume intent.

Phase 295 records one immutable, secret-free binding that authorizes exactly one
expected Phase 289 ``resume`` operation-intent identity for one exact Phase 294
recovery resume preparation.  The binding is persisted or resolved *before* the
Phase 289 intent is loaded or materialized, so a crash between the two steps
leaves a recoverable state in which the binding alone remains authoritative.

``state`` ``"authorized"`` means only that this exact Phase 294 preparation
authorizes the exact expected Phase 289 resume intent identity.  It does not
mean that the intent has been durably materialized, that a start has been
acquired, that provider execution is authorized, that reconciliation is
complete, or that ``fresh`` may be replayed.

Phase 295 acquires no Phase 290 start marker, executes no resume, reconciles
nothing, and performs no provider, transport, or network work.
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
    persist_external_publication_operation_intent,
)
from .external_publication_recovery_resume_preparation import (
    ExternalPublicationRecoveryResumePreparation,
    ExternalPublicationRecoveryResumePreparationError,
    external_publication_recovery_resume_preparation_digest,
    load_external_publication_recovery_resume_preparation,
)

_BINDING_ERROR_MESSAGE = (
    "external publication recovery resume intent binding is invalid"
)
_PERSISTENCE_ERROR_MESSAGE = (
    "external publication recovery resume intent binding persistence failed"
)
_LOAD_ERROR_MESSAGE = (
    "external publication recovery resume intent binding could not be loaded"
)
_BINDING_SCHEMA_VERSION = "external-publication-recovery-resume-intent-binding.v1"
_INTENT_SCHEMA_VERSION = "external-publication-operation-intent.v1"
_BINDING_KEYS = frozenset(
    {
        "operation",
        "operation_intent_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "recovery_decision_sha256",
        "recovery_kind",
        "resume_preparation_sha256",
        "schema_version",
        "source_operation",
        "state",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_MAX_BINDING_BYTES = 4096
_SOURCE_OPERATIONS = frozenset({"fresh", "resume"})
_RECOVERY_KINDS = frozenset({"already_acquired", "reconciliation_mismatch"})
_OPERATIONS = frozenset({"resume"})
_STATES = frozenset({"authorized"})
_PREPARATION_SCHEMA_VERSION = "external-publication-recovery-resume-preparation.v1"
_PREPARATION_SOURCE_OPERATIONS = frozenset({"fresh", "resume"})
_PREPARATION_RECOVERY_KINDS = frozenset({"already_acquired", "reconciliation_mismatch"})
_PREPARATION_TARGET_OPERATIONS = frozenset({"resume"})
_PREPARATION_STATES = frozenset({"prepared"})
_INTENT_OPERATIONS = frozenset({"fresh", "resume"})

Classification = Literal[
    "configuration",
    "path_type",
    "path_conflict",
    "preparation_contract",
    "preparation_digest",
    "intent_contract",
    "intent_digest",
    "intent_conflict",
    "serialization",
    "encoding",
    "parent",
    "target",
    "create",
    "ambiguous",
    "conflict",
    "load",
    "parse",
    "keys",
    "noncanonical",
    "dependency_error",
]

PreparationLoader = Callable[[Path], object]
PreparationDigestFunction = Callable[
    [ExternalPublicationRecoveryResumePreparation], object
]
IntentLoader = Callable[[Path], object]
IntentDigestFunction = Callable[[ExternalPublicationOperationIntent], object]
IntentPersistFunction = Callable[[Path, ExternalPublicationOperationIntent], object]

_KNOWN_PREDECESSOR_ERRORS = (
    ExternalPublicationRecoveryResumePreparationError,
    ExternalPublicationOperationIntentError,
)


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeIntentBindingFailureDetail:
    """Detail-safe classification for one resume-intent-binding failure."""

    classification: Classification


class ExternalPublicationRecoveryResumeIntentBindingError(ValueError):
    """Raised when a resume-intent binding or request is not exact."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_BINDING_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeIntentBindingFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeIntentBindingCompatibilityError(
    ExternalPublicationRecoveryResumeIntentBindingError
):
    """Raised when a path, preparation, or intent is incompatible."""


class ExternalPublicationRecoveryResumeIntentBindingPersistenceError(
    ExternalPublicationRecoveryResumeIntentBindingError
):
    """Raised when resume-intent-binding durability cannot be proven."""

    def __init__(self, classification: Classification = "target") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeIntentBindingFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeIntentBindingConflictError(
    ExternalPublicationRecoveryResumeIntentBindingPersistenceError
):
    """Raised when an occupied binding target is not the exact record."""


class ExternalPublicationRecoveryResumeIntentBindingLoadError(
    ExternalPublicationRecoveryResumeIntentBindingError
):
    """Raised when a resume-intent-binding sidecar is not an exact record."""

    def __init__(self, classification: Classification = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeIntentBindingFailureDetail(
            classification
        )


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeIntentBinding:
    """Immutable, secret-free recovery resume-intent provenance binding.

    ``authorized`` records only that this exact Phase 294 preparation authorizes
    the exact expected Phase 289 resume intent identity.  It records no start,
    no execution, no reconciliation, and no replay permission.
    """

    schema_version: Literal["external-publication-recovery-resume-intent-binding.v1"]
    resume_preparation_sha256: str
    recovery_decision_sha256: str
    publication_approval_sha256: str
    publication_plan_sha256: str
    operation_intent_sha256: str
    source_operation: Literal["fresh", "resume"]
    recovery_kind: Literal["already_acquired", "reconciliation_mismatch"]
    operation: Literal["resume"]
    state: Literal["authorized"]

    def __post_init__(self) -> None:
        _validate_binding(self)


def serialize_external_publication_recovery_resume_intent_binding_canonical(
    binding: ExternalPublicationRecoveryResumeIntentBinding,
) -> str:
    """Serialize one exact resume-intent binding as compact deterministic JSON."""
    _validate_binding(binding)
    try:
        return json.dumps(
            {
                "operation": binding.operation,
                "operation_intent_sha256": binding.operation_intent_sha256,
                "publication_approval_sha256": binding.publication_approval_sha256,
                "publication_plan_sha256": binding.publication_plan_sha256,
                "recovery_decision_sha256": binding.recovery_decision_sha256,
                "recovery_kind": binding.recovery_kind,
                "resume_preparation_sha256": binding.resume_preparation_sha256,
                "schema_version": binding.schema_version,
                "source_operation": binding.source_operation,
                "state": binding.state,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except ExternalPublicationRecoveryResumeIntentBindingError:
        raise
    except Exception:
        _raise_binding("serialization")


def external_publication_recovery_resume_intent_binding_canonical_bytes(
    binding: ExternalPublicationRecoveryResumeIntentBinding,
) -> bytes:
    """Return exact canonical resume-intent-binding JSON as UTF-8 bytes."""
    try:
        return serialize_external_publication_recovery_resume_intent_binding_canonical(
            binding
        ).encode("utf-8")
    except ExternalPublicationRecoveryResumeIntentBindingError:
        raise
    except UnicodeError:
        _raise_binding("encoding")
    except Exception:
        _raise_binding("encoding")


def external_publication_recovery_resume_intent_binding_digest(
    binding: ExternalPublicationRecoveryResumeIntentBinding,
) -> str:
    """Return SHA-256 over exact canonical resume-intent-binding UTF-8 bytes."""
    return sha256(
        external_publication_recovery_resume_intent_binding_canonical_bytes(binding)
    ).hexdigest()


def load_external_publication_recovery_resume_intent_binding(
    path: Path,
) -> ExternalPublicationRecoveryResumeIntentBinding:
    """Read and strictly revalidate one immutable canonical binding record."""
    _validate_load_path(path)
    try:
        with path.open("rb") as handle:
            contents = handle.read(_MAX_BINDING_BYTES + 1)
    except Exception:
        _raise_load("target")
    if type(contents) is not bytes:
        _raise_load("target")
    if len(contents) > _MAX_BINDING_BYTES:
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

    binding = _parse_binding(value)
    try:
        canonical = external_publication_recovery_resume_intent_binding_canonical_bytes(
            binding
        )
    except ExternalPublicationRecoveryResumeIntentBindingError:
        _raise_load("load")
    except Exception:
        _raise_load("load")
    if canonical != contents:
        _raise_load("noncanonical")
    return binding


def persist_external_publication_recovery_resume_intent_binding(
    path: Path,
    binding: ExternalPublicationRecoveryResumeIntentBinding,
) -> None:
    """Durably append-only persist one exact resume-intent binding record.

    The canonical bytes are derived and validated first.  A new target is
    created exclusively, written in full, flushed, file-fsynced, closed, and
    then parent-directory-fsynced.  An identical existing target is an
    idempotent success; any other existing content is a fixed conflict and is
    never overwritten, truncated, deleted, or renamed over.  An uncertain
    failure after exclusive creation retains the artifact with no cleanup and
    no retry.
    """
    _validate_persistence_target(path)
    _validate_binding(binding)
    try:
        contents = external_publication_recovery_resume_intent_binding_canonical_bytes(
            binding
        )
    except ExternalPublicationRecoveryResumeIntentBindingError:
        raise
    except Exception:
        _raise_persistence("serialization")
    _write_binding(path, contents)


def materialize_and_bind_external_publication_recovery_resume_intent(
    *,
    resume_preparation_path: Path,
    resume_intent_binding_path: Path,
    resume_intent_path: Path,
    preparation_loader: PreparationLoader = (
        load_external_publication_recovery_resume_preparation
    ),
    preparation_digest_function: PreparationDigestFunction = (
        external_publication_recovery_resume_preparation_digest
    ),
    intent_loader: IntentLoader = load_external_publication_operation_intent,
    intent_digest_function: IntentDigestFunction = (
        external_publication_operation_intent_digest
    ),
    intent_persist_function: IntentPersistFunction = (
        persist_external_publication_operation_intent
    ),
) -> ExternalPublicationRecoveryResumeIntentBinding:
    """Bind one exact Phase 294 preparation to one exact Phase 289 resume intent.

    Phase 295 strict-loads the Phase 294 preparation exactly once through the
    caller's exact ``resume_preparation_path`` identity and locally revalidates
    every model field and cross-field invariant.  The preparation digest is
    computed exactly once from the exact loaded object.  The expected Phase 289
    ``resume`` intent is constructed only from the validated preparation
    approval/plan lineage; no approval object is accepted or reloaded as
    authority and no Phase 289 builder is called.  Its digest is computed
    exactly once from the exact constructed object.

    The Phase 295 binding is then persisted or resolved *first*, before the
    Phase 289 intent target is loaded or mutated, so a crash between the two
    steps leaves the exact binding as the sole durable authority.  Only after
    binding durability is established is a pre-existing intent strict-loaded
    once and accepted when it equals the exact expected intent, or the expected
    intent persisted exactly once.

    Success is returned only after the exact intent is proven durable in this
    invocation.  No Phase 290 start marker is created, no Phase 291/292/293/294
    orchestration is called, no provider/transport/network work happens, and a
    Phase 289 intent digest alone is never treated as recovery authorization.
    """
    _preflight(
        resume_preparation_path=resume_preparation_path,
        resume_intent_binding_path=resume_intent_binding_path,
        resume_intent_path=resume_intent_path,
        preparation_loader=preparation_loader,
        preparation_digest_function=preparation_digest_function,
        intent_loader=intent_loader,
        intent_digest_function=intent_digest_function,
        intent_persist_function=intent_persist_function,
    )

    preparation = _strict_load_preparation(preparation_loader, resume_preparation_path)
    _validate_preparation_contract(preparation)
    preparation_digest = _preparation_digest_once(
        preparation_digest_function, preparation
    )

    expected_intent = _construct_expected_intent(preparation)
    _validate_intent_contract(expected_intent)
    intent_digest = _intent_digest_once(intent_digest_function, expected_intent)

    constructed = ExternalPublicationRecoveryResumeIntentBinding(
        schema_version=_BINDING_SCHEMA_VERSION,  # type: ignore[arg-type]
        resume_preparation_sha256=preparation_digest,
        recovery_decision_sha256=preparation.recovery_decision_sha256,  # type: ignore[union-attr]
        publication_approval_sha256=preparation.publication_approval_sha256,  # type: ignore[union-attr]
        publication_plan_sha256=preparation.publication_plan_sha256,  # type: ignore[union-attr]
        operation_intent_sha256=intent_digest,
        source_operation=preparation.source_operation,  # type: ignore[union-attr]
        recovery_kind=preparation.recovery_kind,  # type: ignore[union-attr]
        operation="resume",
        state="authorized",
    )

    if _binding_target_present(resume_intent_binding_path):
        binding = _resolve_existing_binding(resume_intent_binding_path, constructed)
    else:
        persist_external_publication_recovery_resume_intent_binding(
            resume_intent_binding_path, constructed
        )
        binding = constructed

    _materialize_intent(
        resume_intent_path=resume_intent_path,
        expected_intent=expected_intent,
        intent_loader=intent_loader,
        intent_persist_function=intent_persist_function,
    )
    return binding


def _preflight(
    *,
    resume_preparation_path: object,
    resume_intent_binding_path: object,
    resume_intent_path: object,
    preparation_loader: object,
    preparation_digest_function: object,
    intent_loader: object,
    intent_digest_function: object,
    intent_persist_function: object,
) -> None:
    if (
        type(resume_preparation_path) is not _PATH_TYPE
        or type(resume_intent_binding_path) is not _PATH_TYPE
        or type(resume_intent_path) is not _PATH_TYPE
    ):
        _raise_binding("path_type")
    if (
        not callable(preparation_loader)
        or not callable(preparation_digest_function)
        or not callable(intent_loader)
        or not callable(intent_digest_function)
        or not callable(intent_persist_function)
    ):
        _raise_binding("configuration")
    if (
        len(
            {
                resume_preparation_path,
                resume_intent_binding_path,
                resume_intent_path,
            }
        )
        != 3
    ):
        _raise_binding("path_conflict")
    _validate_preflight_target(resume_intent_binding_path)
    _validate_preflight_target(resume_intent_path)


def _validate_preflight_target(path: object) -> None:
    try:
        if not path.parent.exists() or not path.parent.is_dir():  # type: ignore[union-attr]
            _raise_binding("parent")
        if (
            path.is_symlink()  # type: ignore[union-attr]
            or path.is_dir()  # type: ignore[union-attr]
            or (path.exists() and not path.is_file())  # type: ignore[union-attr]
        ):
            _raise_binding("target")
    except ExternalPublicationRecoveryResumeIntentBindingError:
        raise
    except Exception:
        _raise_binding("target")


def _strict_load_preparation(
    loader: PreparationLoader, preparation_path: Path
) -> object:
    try:
        preparation = loader(preparation_path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_binding("dependency_error")
    if type(preparation) is not ExternalPublicationRecoveryResumePreparation:
        _raise_binding("preparation_contract")
    return preparation


def _preparation_digest_once(
    digest_function: PreparationDigestFunction, preparation: object
) -> str:
    try:
        digest = digest_function(preparation)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_binding("dependency_error")
    if not _is_sha256(digest):
        _raise_binding("preparation_digest")
    return digest  # type: ignore[return-value]


def _validate_preparation_contract(preparation: object) -> None:
    if type(preparation) is not ExternalPublicationRecoveryResumePreparation:
        _raise_binding("preparation_contract")
    try:
        schema_version = preparation.schema_version  # type: ignore[union-attr]
        decision_digest = preparation.recovery_decision_sha256  # type: ignore[union-attr]
        lifecycle_digest = preparation.lifecycle_outcome_sha256  # type: ignore[union-attr]
        start_digest = preparation.operation_start_sha256  # type: ignore[union-attr]
        approval_digest = preparation.publication_approval_sha256  # type: ignore[union-attr]
        plan_digest = preparation.publication_plan_sha256  # type: ignore[union-attr]
        source_operation = preparation.source_operation  # type: ignore[union-attr]
        recovery_kind = preparation.recovery_kind  # type: ignore[union-attr]
        target_operation = preparation.target_operation  # type: ignore[union-attr]
        state = preparation.state  # type: ignore[union-attr]
    except ExternalPublicationRecoveryResumeIntentBindingError:
        raise
    except Exception:
        _raise_binding("preparation_contract")

    if type(schema_version) is not str or schema_version != _PREPARATION_SCHEMA_VERSION:
        _raise_binding("preparation_contract")
    if not _is_sha256(decision_digest):
        _raise_binding("preparation_contract")
    if not _is_sha256(lifecycle_digest):
        _raise_binding("preparation_contract")
    if not _is_sha256(start_digest):
        _raise_binding("preparation_contract")
    if not _is_sha256(approval_digest):
        _raise_binding("preparation_contract")
    if not _is_sha256(plan_digest):
        _raise_binding("preparation_contract")
    if (
        type(source_operation) is not str
        or source_operation not in _PREPARATION_SOURCE_OPERATIONS
    ):
        _raise_binding("preparation_contract")
    if (
        type(recovery_kind) is not str
        or recovery_kind not in _PREPARATION_RECOVERY_KINDS
    ):
        _raise_binding("preparation_contract")
    if recovery_kind == "reconciliation_mismatch" and source_operation != "resume":
        _raise_binding("preparation_contract")
    if (
        type(target_operation) is not str
        or target_operation not in _PREPARATION_TARGET_OPERATIONS
    ):
        _raise_binding("preparation_contract")
    if type(state) is not str or state not in _PREPARATION_STATES:
        _raise_binding("preparation_contract")


def _construct_expected_intent(
    preparation: object,
) -> ExternalPublicationOperationIntent:
    try:
        return ExternalPublicationOperationIntent(
            schema_version=_INTENT_SCHEMA_VERSION,  # type: ignore[arg-type]
            publication_approval_sha256=preparation.publication_approval_sha256,  # type: ignore[union-attr]
            publication_plan_sha256=preparation.publication_plan_sha256,  # type: ignore[union-attr]
            operation="resume",
        )
    except ExternalPublicationRecoveryResumeIntentBindingError:
        raise
    except Exception:
        _raise_binding("intent_contract")


def _validate_intent_contract(intent: object) -> None:
    if type(intent) is not ExternalPublicationOperationIntent:
        _raise_binding("intent_contract")
    try:
        schema_version = intent.schema_version  # type: ignore[union-attr]
        approval_digest = intent.publication_approval_sha256  # type: ignore[union-attr]
        plan_digest = intent.publication_plan_sha256  # type: ignore[union-attr]
        operation = intent.operation  # type: ignore[union-attr]
    except ExternalPublicationRecoveryResumeIntentBindingError:
        raise
    except Exception:
        _raise_binding("intent_contract")

    if type(schema_version) is not str or schema_version != _INTENT_SCHEMA_VERSION:
        _raise_binding("intent_contract")
    if not _is_sha256(approval_digest):
        _raise_binding("intent_contract")
    if not _is_sha256(plan_digest):
        _raise_binding("intent_contract")
    if type(operation) is not str or operation not in _INTENT_OPERATIONS:
        _raise_binding("intent_contract")


def _intent_digest_once(digest_function: IntentDigestFunction, intent: object) -> str:
    try:
        digest = digest_function(intent)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_binding("dependency_error")
    if not _is_sha256(digest):
        _raise_binding("intent_digest")
    return digest  # type: ignore[return-value]


def _binding_target_present(path: Path) -> bool:
    try:
        return path.is_symlink() or path.exists()
    except Exception:
        _raise_load("target")


def _resolve_existing_binding(
    resume_intent_binding_path: Path,
    constructed: ExternalPublicationRecoveryResumeIntentBinding,
) -> ExternalPublicationRecoveryResumeIntentBinding:
    loaded = load_external_publication_recovery_resume_intent_binding(
        resume_intent_binding_path
    )
    if type(loaded) is not ExternalPublicationRecoveryResumeIntentBinding:
        _raise_binding("load")
    if loaded != constructed:
        _raise_conflict()
    return loaded


def _materialize_intent(
    *,
    resume_intent_path: Path,
    expected_intent: ExternalPublicationOperationIntent,
    intent_loader: IntentLoader,
    intent_persist_function: IntentPersistFunction,
) -> None:
    if _intent_target_present(resume_intent_path):
        loaded = _strict_load_intent(intent_loader, resume_intent_path)
        _validate_intent_contract(loaded)
        if loaded != expected_intent:
            _raise_binding("intent_conflict")
        return
    _persist_intent_once(intent_persist_function, resume_intent_path, expected_intent)


def _intent_target_present(path: Path) -> bool:
    try:
        return path.is_symlink() or path.exists()
    except Exception:
        _raise_binding("target")


def _strict_load_intent(loader: IntentLoader, intent_path: Path) -> object:
    try:
        intent = loader(intent_path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_binding("dependency_error")
    if type(intent) is not ExternalPublicationOperationIntent:
        _raise_binding("intent_contract")
    return intent


def _persist_intent_once(
    persist_function: IntentPersistFunction,
    intent_path: Path,
    intent: ExternalPublicationOperationIntent,
) -> None:
    try:
        persist_function(intent_path, intent)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_binding("dependency_error")


def _validate_binding(binding: object) -> None:
    if type(binding) is not ExternalPublicationRecoveryResumeIntentBinding:
        _raise_binding("configuration")
    try:
        _check_binding(binding)
    except ExternalPublicationRecoveryResumeIntentBindingError:
        raise
    except Exception:
        _raise_binding("configuration")


def _check_binding(binding: object) -> None:
    schema_version = binding.schema_version  # type: ignore[union-attr]
    preparation_digest = binding.resume_preparation_sha256  # type: ignore[union-attr]
    decision_digest = binding.recovery_decision_sha256  # type: ignore[union-attr]
    approval_digest = binding.publication_approval_sha256  # type: ignore[union-attr]
    plan_digest = binding.publication_plan_sha256  # type: ignore[union-attr]
    intent_digest = binding.operation_intent_sha256  # type: ignore[union-attr]
    source_operation = binding.source_operation  # type: ignore[union-attr]
    recovery_kind = binding.recovery_kind  # type: ignore[union-attr]
    operation = binding.operation  # type: ignore[union-attr]
    state = binding.state  # type: ignore[union-attr]

    if type(schema_version) is not str or schema_version != _BINDING_SCHEMA_VERSION:
        _raise_binding("configuration")
    if not _is_sha256(preparation_digest):
        _raise_binding("configuration")
    if not _is_sha256(decision_digest):
        _raise_binding("configuration")
    if not _is_sha256(approval_digest):
        _raise_binding("configuration")
    if not _is_sha256(plan_digest):
        _raise_binding("configuration")
    if not _is_sha256(intent_digest):
        _raise_binding("configuration")
    if type(source_operation) is not str or source_operation not in _SOURCE_OPERATIONS:
        _raise_binding("configuration")
    if type(recovery_kind) is not str or recovery_kind not in _RECOVERY_KINDS:
        _raise_binding("configuration")
    if recovery_kind == "reconciliation_mismatch" and source_operation != "resume":
        _raise_binding("configuration")
    if type(operation) is not str or operation not in _OPERATIONS:
        _raise_binding("configuration")
    if type(state) is not str or state not in _STATES:
        _raise_binding("configuration")


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
    except ExternalPublicationRecoveryResumeIntentBindingPersistenceError:
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
    except ExternalPublicationRecoveryResumeIntentBindingLoadError:
        raise
    except Exception:
        _raise_load("target")


def _write_binding(path: Path, contents: bytes) -> None:
    try:
        handle = path.open("xb")
    except FileExistsError:
        _verify_existing_binding(path, contents)
        return
    except OSError as error:
        if error.errno == errno.EEXIST:
            _verify_existing_binding(path, contents)
            return
        _raise_persistence("create")
    except Exception:
        _raise_persistence("create")

    _persist_new_binding(handle, path.parent, contents)


def _persist_new_binding(handle: object, directory: Path, contents: bytes) -> None:
    try:
        with _binding_handle_scope(handle) as active_handle:
            written = active_handle.write(contents)  # type: ignore[attr-defined]
            if type(written) is not int or written != len(contents):
                raise OSError("short resume intent binding write")
            active_handle.flush()  # type: ignore[attr-defined]
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_binding_directory(directory)
    except Exception:
        _raise_persistence("ambiguous")


def _verify_existing_binding(path: Path, contents: bytes) -> None:
    try:
        if (
            path.is_symlink()
            or not path.exists()
            or path.is_dir()
            or not path.is_file()
        ):
            _raise_persistence("target")
        with path.open("rb") as handle:
            existing = handle.read(_MAX_BINDING_BYTES + 1)
        if type(existing) is not bytes:
            _raise_persistence("target")
    except ExternalPublicationRecoveryResumeIntentBindingPersistenceError:
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
        with _binding_handle_scope(handle) as active_handle:
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")
    try:
        _fsync_binding_directory(path.parent)
    except Exception:
        _raise_persistence("ambiguous")


@contextmanager
def _binding_handle_scope(handle: object) -> Iterator[object]:
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
            raise OSError("resume intent binding handle cannot close")
        close()


def _fsync_binding_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_binding(
    value: object,
) -> ExternalPublicationRecoveryResumeIntentBinding:
    if type(value) is not dict or frozenset(value) != _BINDING_KEYS:
        _raise_load("keys")
    try:
        return ExternalPublicationRecoveryResumeIntentBinding(
            schema_version=value["schema_version"],
            resume_preparation_sha256=value["resume_preparation_sha256"],
            recovery_decision_sha256=value["recovery_decision_sha256"],
            publication_approval_sha256=value["publication_approval_sha256"],
            publication_plan_sha256=value["publication_plan_sha256"],
            operation_intent_sha256=value["operation_intent_sha256"],
            source_operation=value["source_operation"],
            recovery_kind=value["recovery_kind"],
            operation=value["operation"],
            state=value["state"],
        )
    except Exception:
        _raise_load("load")


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


def _raise_binding(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeIntentBindingCompatibilityError(
        classification
    ) from None


def _raise_persistence(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeIntentBindingPersistenceError(
        classification
    ) from None


def _raise_conflict() -> NoReturn:
    raise ExternalPublicationRecoveryResumeIntentBindingConflictError(
        "conflict"
    ) from None


def _raise_load(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeIntentBindingLoadError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationRecoveryResumeIntentBinding",
    "ExternalPublicationRecoveryResumeIntentBindingCompatibilityError",
    "ExternalPublicationRecoveryResumeIntentBindingConflictError",
    "ExternalPublicationRecoveryResumeIntentBindingError",
    "ExternalPublicationRecoveryResumeIntentBindingFailureDetail",
    "ExternalPublicationRecoveryResumeIntentBindingLoadError",
    "ExternalPublicationRecoveryResumeIntentBindingPersistenceError",
    "external_publication_recovery_resume_intent_binding_canonical_bytes",
    "external_publication_recovery_resume_intent_binding_digest",
    "load_external_publication_recovery_resume_intent_binding",
    "materialize_and_bind_external_publication_recovery_resume_intent",
    "persist_external_publication_recovery_resume_intent_binding",
    "serialize_external_publication_recovery_resume_intent_binding_canonical",
]
