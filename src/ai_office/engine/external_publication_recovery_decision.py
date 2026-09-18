"""Durable explicit external-publication recovery decision evidence.

Phase 293 adds one append-only, immutable, explicit operator decision boundary
on top of the Phase 292 durable lifecycle outcome.  It consumes only an exact
durable Phase 292 lifecycle outcome whose state is ``recovery_required``,
strictly revalidates the exact Phase 290 start lineage, and records exactly one
explicit operator decision.  It never executes recovery.

Two decisions are allowed:

``stop``
    Terminate this recovery path with no automatic action.  Phase 293 records
    only the decision, performs no provider call, no resume preparation, no
    Phase 291/292 execution, and no fresh replay.

``authorize_resume_preparation``
    Authorize only a *future* explicit phase to prepare a new explicit
    resume-operation lineage bound to this exact recovery decision.  It is
    **not** permission to execute a provider call, to call Phase 291/292, to
    acquire a new Phase 290 start marker, to create a Phase 289 resume intent,
    to reconcile automatically, or to replay fresh.  It does not guarantee that
    a resume is possible.

``completed`` Phase 292 outcomes never enter Phase 293.  The recovery kind is
derived from the strict-loaded Phase 292 lifecycle model plus the exact Phase
290 start lineage; it is never caller supplied.  Phase 293 stops after
returning/persisting the decision evidence.
"""

from __future__ import annotations

import errno
import json
import os
import re
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn

from .external_publication_operation_lifecycle_outcome import (
    ExternalPublicationOperationLifecycleOutcome,
    ExternalPublicationOperationLifecycleOutcomeError,
    external_publication_operation_lifecycle_outcome_digest,
    load_external_publication_operation_lifecycle_outcome,
)
from .external_publication_operation_start import (
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartError,
    external_publication_operation_start_digest,
    load_external_publication_operation_start,
)

_DECISION_ERROR_MESSAGE = "external publication recovery decision is invalid"
_PERSISTENCE_ERROR_MESSAGE = "external publication recovery decision persistence failed"
_LOAD_ERROR_MESSAGE = "external publication recovery decision could not be loaded"
_DECISION_SCHEMA_VERSION = "external-publication-recovery-decision.v1"
_DECISION_KEYS = frozenset(
    {
        "decision",
        "decided_by",
        "decision_id",
        "lifecycle_outcome_sha256",
        "operation_start_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "recovery_kind",
        "schema_version",
        "source_operation",
        "state",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_MAX_DECISION_BYTES = 4096
_MAX_METADATA_LENGTH = 256
_SOURCE_OPERATIONS = frozenset({"fresh", "resume"})
_RECOVERY_KINDS = frozenset({"already_acquired", "reconciliation_mismatch"})
_DECISIONS = frozenset({"stop", "authorize_resume_preparation"})
_STATES = frozenset({"decided"})
_LIFECYCLE_STATES = frozenset({"completed", "recovery_required"})
_RESULT_KINDS = frozenset({"execution_result", "reconciliation", "none"})

Classification = Literal[
    "configuration",
    "path_type",
    "decision",
    "operator_metadata",
    "lifecycle_contract",
    "lifecycle_state",
    "lifecycle_digest",
    "start_contract",
    "start_lineage",
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

LifecycleLoader = Callable[[Path], object]
LifecycleDigestFunction = Callable[
    [ExternalPublicationOperationLifecycleOutcome], object
]
StartLoader = Callable[[Path], object]
StartDigestFunction = Callable[[ExternalPublicationOperationStart], object]

_KNOWN_PREDECESSOR_ERRORS = (
    ExternalPublicationOperationLifecycleOutcomeError,
    ExternalPublicationOperationStartError,
)


@dataclass(frozen=True)
class ExternalPublicationRecoveryDecisionFailureDetail:
    """Detail-safe classification for one recovery decision failure."""

    classification: Classification


class ExternalPublicationRecoveryDecisionError(ValueError):
    """Raised when a recovery decision record or request is not exact."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_DECISION_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryDecisionFailureDetail(classification)


class ExternalPublicationRecoveryDecisionCompatibilityError(
    ExternalPublicationRecoveryDecisionError
):
    """Raised when a path, decision, metadata, or predecessor is incompatible."""


class ExternalPublicationRecoveryDecisionPersistenceError(
    ExternalPublicationRecoveryDecisionError
):
    """Raised when recovery decision durability cannot be proven."""

    def __init__(self, classification: Classification = "target") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryDecisionFailureDetail(classification)


class ExternalPublicationRecoveryDecisionConflictError(
    ExternalPublicationRecoveryDecisionPersistenceError
):
    """Raised when an occupied decision target is not the exact decision."""


class ExternalPublicationRecoveryDecisionLoadError(
    ExternalPublicationRecoveryDecisionError
):
    """Raised when a recovery decision sidecar is not an exact record."""

    def __init__(self, classification: Classification = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryDecisionFailureDetail(classification)


@dataclass(frozen=True)
class ExternalPublicationRecoveryDecision:
    """Immutable, secret-free explicit operator recovery decision evidence."""

    schema_version: Literal["external-publication-recovery-decision.v1"]
    lifecycle_outcome_sha256: str
    operation_start_sha256: str
    publication_approval_sha256: str
    publication_plan_sha256: str
    source_operation: Literal["fresh", "resume"]
    recovery_kind: Literal["already_acquired", "reconciliation_mismatch"]
    decision: Literal["stop", "authorize_resume_preparation"]
    decided_by: str
    decision_id: str
    state: Literal["decided"]

    def __post_init__(self) -> None:
        _validate_recovery_decision(self)


def serialize_external_publication_recovery_decision_canonical(
    decision: ExternalPublicationRecoveryDecision,
) -> str:
    """Serialize one exact recovery decision as compact deterministic JSON."""
    _validate_recovery_decision(decision)
    try:
        return json.dumps(
            {
                "decision": decision.decision,
                "decided_by": decision.decided_by,
                "decision_id": decision.decision_id,
                "lifecycle_outcome_sha256": decision.lifecycle_outcome_sha256,
                "operation_start_sha256": decision.operation_start_sha256,
                "publication_approval_sha256": decision.publication_approval_sha256,
                "publication_plan_sha256": decision.publication_plan_sha256,
                "recovery_kind": decision.recovery_kind,
                "schema_version": decision.schema_version,
                "source_operation": decision.source_operation,
                "state": decision.state,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except ExternalPublicationRecoveryDecisionError:
        raise
    except Exception:
        _raise_decision("serialization")


def external_publication_recovery_decision_canonical_bytes(
    decision: ExternalPublicationRecoveryDecision,
) -> bytes:
    """Return exact canonical recovery decision JSON encoded as UTF-8 bytes."""
    try:
        return serialize_external_publication_recovery_decision_canonical(
            decision
        ).encode("utf-8")
    except ExternalPublicationRecoveryDecisionError:
        raise
    except UnicodeError:
        _raise_decision("encoding")
    except Exception:
        _raise_decision("encoding")


def external_publication_recovery_decision_digest(
    decision: ExternalPublicationRecoveryDecision,
) -> str:
    """Return SHA-256 over exact canonical recovery decision UTF-8 bytes."""
    return sha256(
        external_publication_recovery_decision_canonical_bytes(decision)
    ).hexdigest()


def load_external_publication_recovery_decision(
    path: Path,
) -> ExternalPublicationRecoveryDecision:
    """Read and strictly revalidate one immutable canonical decision record."""
    _validate_load_path(path)
    try:
        with path.open("rb") as handle:
            contents = handle.read(_MAX_DECISION_BYTES + 1)
    except Exception:
        _raise_load("target")
    if type(contents) is not bytes:
        _raise_load("target")
    if len(contents) > _MAX_DECISION_BYTES:
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

    decision = _parse_decision(value)
    try:
        canonical = external_publication_recovery_decision_canonical_bytes(decision)
    except ExternalPublicationRecoveryDecisionError:
        _raise_load("load")
    except Exception:
        _raise_load("load")
    if canonical != contents:
        _raise_load("noncanonical")
    return decision


def persist_external_publication_recovery_decision(
    path: Path,
    decision: ExternalPublicationRecoveryDecision,
) -> None:
    """Durably append-only persist one exact recovery decision record.

    The canonical bytes are derived and validated first.  A new target is
    created exclusively, written in full, flushed, file-fsynced, closed, and
    then parent-directory-fsynced.  An identical existing target is an
    idempotent success; any other existing content is a fixed conflict and is
    never overwritten, truncated, deleted, or renamed over.  An uncertain
    failure after exclusive creation retains the artifact with no cleanup and
    no retry.
    """
    _validate_persistence_target(path)
    _validate_recovery_decision(decision)
    try:
        contents = external_publication_recovery_decision_canonical_bytes(decision)
    except ExternalPublicationRecoveryDecisionError:
        raise
    except Exception:
        _raise_persistence("serialization")
    _write_recovery_decision(path, contents)


def decide_and_persist_external_publication_recovery(
    *,
    lifecycle_outcome_path: Path,
    start_path: Path,
    recovery_decision_path: Path,
    decision: Literal["stop", "authorize_resume_preparation"],
    decided_by: str,
    decision_id: str,
    lifecycle_loader: LifecycleLoader = (
        load_external_publication_operation_lifecycle_outcome
    ),
    lifecycle_digest_function: LifecycleDigestFunction = (
        external_publication_operation_lifecycle_outcome_digest
    ),
    start_loader: StartLoader = load_external_publication_operation_start,
    start_digest_function: StartDigestFunction = (
        external_publication_operation_start_digest
    ),
) -> ExternalPublicationRecoveryDecision:
    """Record exactly one explicit operator decision on a Phase 292 recovery.

    Phase 293 strict-loads the Phase 292 lifecycle outcome exactly once through
    the caller's exact ``lifecycle_outcome_path`` identity, requires state
    ``recovery_required`` (``completed`` is rejected), and derives the recovery
    kind from the validated lifecycle model only.  It then strict-loads the
    Phase 290 start exactly once and requires the start digest to equal the
    lifecycle ``operation_start_sha256`` with matching operation, approval, and
    plan lineage.

    The requested ``decision`` is either ``stop`` or
    ``authorize_resume_preparation`` and is never derived from ambient state:

    - ``stop`` records a terminal no-action route and performs no provider call,
      no resume preparation, no Phase 291/292 execution, and no fresh replay;
    - ``authorize_resume_preparation`` only authorizes a *future* explicit phase
      to prepare a new resume-operation lineage bound to this exact decision.  It
      is not execution permission, does not call Phase 291/292, does not create
      a Phase 289 resume intent, and does not acquire a new Phase 290 start
      marker.

    An existing decision is strict-loaded once and returned by identity only
    when every lineage and requested field matches exactly; any difference is a
    fixed conflict and leaves the existing bytes unchanged.  Otherwise the
    constructed decision is persisted exactly once with no retry.
    """
    _preflight(
        lifecycle_outcome_path=lifecycle_outcome_path,
        start_path=start_path,
        recovery_decision_path=recovery_decision_path,
        decision=decision,
        decided_by=decided_by,
        decision_id=decision_id,
        lifecycle_loader=lifecycle_loader,
        lifecycle_digest_function=lifecycle_digest_function,
        start_loader=start_loader,
        start_digest_function=start_digest_function,
    )

    lifecycle = _strict_load_lifecycle(lifecycle_loader, lifecycle_outcome_path)
    _validate_lifecycle_contract(lifecycle)
    recovery_kind = _derive_recovery_kind(lifecycle)
    lifecycle_digest = _lifecycle_digest_once(lifecycle_digest_function, lifecycle)

    start = _strict_load_start(start_loader, start_path)
    _validate_start_contract(start)
    start_digest = _start_digest_once(start_digest_function, start)
    _validate_start_lineage(
        start,
        lifecycle=lifecycle,
        lifecycle_digest=lifecycle_digest,
        start_digest=start_digest,
    )

    constructed = ExternalPublicationRecoveryDecision(
        schema_version=_DECISION_SCHEMA_VERSION,  # type: ignore[arg-type]
        lifecycle_outcome_sha256=lifecycle_digest,
        operation_start_sha256=lifecycle.operation_start_sha256,
        publication_approval_sha256=lifecycle.publication_approval_sha256,
        publication_plan_sha256=lifecycle.publication_plan_sha256,
        source_operation=lifecycle.operation,
        recovery_kind=recovery_kind,  # type: ignore[arg-type]
        decision=decision,
        decided_by=decided_by,
        decision_id=decision_id,
        state="decided",
    )

    if _decision_target_present(recovery_decision_path):
        return _resolve_existing_decision(recovery_decision_path, constructed)

    persist_external_publication_recovery_decision(recovery_decision_path, constructed)
    return constructed


def _preflight(
    *,
    lifecycle_outcome_path: object,
    start_path: object,
    recovery_decision_path: object,
    decision: object,
    decided_by: object,
    decision_id: object,
    lifecycle_loader: object,
    lifecycle_digest_function: object,
    start_loader: object,
    start_digest_function: object,
) -> None:
    if (
        type(lifecycle_outcome_path) is not _PATH_TYPE
        or type(start_path) is not _PATH_TYPE
        or type(recovery_decision_path) is not _PATH_TYPE
    ):
        _raise_decision("path_type")
    if type(decision) is not str or decision not in _DECISIONS:
        _raise_decision("decision")
    _validate_operator_metadata(decided_by, decision_id)
    if (
        not callable(lifecycle_loader)
        or not callable(lifecycle_digest_function)
        or not callable(start_loader)
        or not callable(start_digest_function)
    ):
        _raise_decision("configuration")
    _validate_persistence_target(recovery_decision_path)


def _validate_operator_metadata(decided_by: object, decision_id: object) -> None:
    for value in (decided_by, decision_id):
        if (
            type(value) is not str
            or not value
            or value != value.strip()
            or len(value) > _MAX_METADATA_LENGTH
        ):
            _raise_decision("operator_metadata")
        if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
            _raise_decision("operator_metadata")


def _strict_load_lifecycle(
    loader: LifecycleLoader,
    lifecycle_outcome_path: Path,
) -> object:
    try:
        lifecycle = loader(lifecycle_outcome_path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_decision("dependency_error")
    if type(lifecycle) is not ExternalPublicationOperationLifecycleOutcome:
        _raise_decision("lifecycle_contract")
    return lifecycle


def _lifecycle_digest_once(
    digest_function: LifecycleDigestFunction,
    lifecycle: object,
) -> str:
    try:
        digest = digest_function(lifecycle)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_decision("dependency_error")
    if not _is_sha256(digest):
        _raise_decision("lifecycle_digest")
    return digest  # type: ignore[return-value]


def _validate_lifecycle_contract(lifecycle: object) -> None:
    if type(lifecycle) is not ExternalPublicationOperationLifecycleOutcome:
        _raise_decision("lifecycle_contract")
    try:
        schema_version = lifecycle.schema_version  # type: ignore[union-attr]
        start_digest = lifecycle.operation_start_sha256  # type: ignore[union-attr]
        approval_digest = lifecycle.publication_approval_sha256  # type: ignore[union-attr]
        plan_digest = lifecycle.publication_plan_sha256  # type: ignore[union-attr]
        operation = lifecycle.operation  # type: ignore[union-attr]
        state = lifecycle.state  # type: ignore[union-attr]
        result_kind = lifecycle.result_kind  # type: ignore[union-attr]
        result_digest = lifecycle.result_sha256  # type: ignore[union-attr]
    except ExternalPublicationRecoveryDecisionError:
        raise
    except Exception:
        _raise_decision("lifecycle_contract")

    if (
        type(schema_version) is not str
        or schema_version != "external-publication-operation-lifecycle-outcome.v1"
    ):
        _raise_decision("lifecycle_contract")
    if not _is_sha256(start_digest):
        _raise_decision("lifecycle_contract")
    if not _is_sha256(approval_digest):
        _raise_decision("lifecycle_contract")
    if not _is_sha256(plan_digest):
        _raise_decision("lifecycle_contract")
    if type(operation) is not str or operation not in _SOURCE_OPERATIONS:
        _raise_decision("lifecycle_contract")
    if type(state) is not str or state not in _LIFECYCLE_STATES:
        _raise_decision("lifecycle_contract")
    if type(result_kind) is not str or result_kind not in _RESULT_KINDS:
        _raise_decision("lifecycle_contract")

    if result_kind == "none":
        if result_digest is not None:
            _raise_decision("lifecycle_contract")
        if state != "recovery_required":
            _raise_decision("lifecycle_contract")
        return
    if not _is_sha256(result_digest):
        _raise_decision("lifecycle_contract")
    if state == "completed":
        if operation == "fresh" and result_kind != "execution_result":
            _raise_decision("lifecycle_contract")
        if operation == "resume" and result_kind != "reconciliation":
            _raise_decision("lifecycle_contract")
        return
    if operation != "resume" or result_kind != "reconciliation":
        _raise_decision("lifecycle_contract")


def _derive_recovery_kind(lifecycle: object) -> str:
    operation = lifecycle.operation  # type: ignore[union-attr]
    state = lifecycle.state  # type: ignore[union-attr]
    result_kind = lifecycle.result_kind  # type: ignore[union-attr]
    result_digest = lifecycle.result_sha256  # type: ignore[union-attr]

    if state == "completed":
        _raise_decision("lifecycle_state")
    if state != "recovery_required":
        _raise_decision("lifecycle_state")
    if result_kind == "none":
        if result_digest is not None:
            _raise_decision("lifecycle_contract")
        return "already_acquired"
    if result_kind == "reconciliation":
        if operation != "resume":
            _raise_decision("lifecycle_state")
        if not _is_sha256(result_digest):
            _raise_decision("lifecycle_contract")
        return "reconciliation_mismatch"
    _raise_decision("lifecycle_state")


def _strict_load_start(loader: StartLoader, start_path: Path) -> object:
    try:
        start = loader(start_path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_decision("dependency_error")
    if type(start) is not ExternalPublicationOperationStart:
        _raise_decision("start_contract")
    return start


def _start_digest_once(
    digest_function: StartDigestFunction,
    start: object,
) -> str:
    try:
        digest = digest_function(start)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_decision("dependency_error")
    if not _is_sha256(digest):
        _raise_decision("start_lineage")
    return digest  # type: ignore[return-value]


def _validate_start_contract(start: object) -> None:
    if type(start) is not ExternalPublicationOperationStart:
        _raise_decision("start_contract")
    try:
        if (
            type(start.schema_version) is not str  # type: ignore[union-attr]
            or start.schema_version  # type: ignore[union-attr]
            != "external-publication-operation-start.v1"
        ):
            _raise_decision("start_contract")
        if not _is_sha256(start.operation_intent_sha256):  # type: ignore[union-attr]
            _raise_decision("start_contract")
        if not _is_sha256(start.publication_approval_sha256):  # type: ignore[union-attr]
            _raise_decision("start_contract")
        if not _is_sha256(start.publication_plan_sha256):  # type: ignore[union-attr]
            _raise_decision("start_contract")
        if (
            type(start.operation) is not str  # type: ignore[union-attr]
            or start.operation not in _SOURCE_OPERATIONS  # type: ignore[union-attr]
        ):
            _raise_decision("start_contract")
        if (
            type(start.state) is not str  # type: ignore[union-attr]
            or start.state != "started"  # type: ignore[union-attr]
        ):
            _raise_decision("start_contract")
    except ExternalPublicationRecoveryDecisionError:
        raise
    except Exception:
        _raise_decision("start_contract")


def _validate_start_lineage(
    start: object,
    *,
    lifecycle: object,
    lifecycle_digest: str,
    start_digest: str,
) -> None:
    try:
        operation = start.operation  # type: ignore[union-attr]
        start_approval = start.publication_approval_sha256  # type: ignore[union-attr]
        start_plan = start.publication_plan_sha256  # type: ignore[union-attr]
    except Exception:
        _raise_decision("start_contract")
    if operation != lifecycle.operation:  # type: ignore[union-attr]
        _raise_decision("start_lineage")
    if (
        start_approval != lifecycle.publication_approval_sha256  # type: ignore[union-attr]
        or start_plan != lifecycle.publication_plan_sha256  # type: ignore[union-attr]
    ):
        _raise_decision("start_lineage")
    if start_digest != lifecycle.operation_start_sha256:  # type: ignore[union-attr]
        _raise_decision("start_lineage")
    if not _is_sha256(lifecycle_digest):
        _raise_decision("lifecycle_digest")


def _decision_target_present(path: Path) -> bool:
    try:
        return path.is_symlink() or path.exists()
    except Exception:
        _raise_load("target")


def _resolve_existing_decision(
    recovery_decision_path: Path,
    constructed: ExternalPublicationRecoveryDecision,
) -> ExternalPublicationRecoveryDecision:
    loaded = load_external_publication_recovery_decision(recovery_decision_path)
    if type(loaded) is not ExternalPublicationRecoveryDecision:
        _raise_decision("load")
    if loaded != constructed:
        _raise_conflict()
    return loaded


def _validate_recovery_decision(decision: object) -> None:
    if type(decision) is not ExternalPublicationRecoveryDecision:
        _raise_decision("configuration")
    try:
        _check_recovery_decision(decision)
    except ExternalPublicationRecoveryDecisionError:
        raise
    except Exception:
        _raise_decision("configuration")


def _check_recovery_decision(decision: object) -> None:
    schema_version = decision.schema_version  # type: ignore[union-attr]
    lifecycle_digest = decision.lifecycle_outcome_sha256  # type: ignore[union-attr]
    start_digest = decision.operation_start_sha256  # type: ignore[union-attr]
    approval_digest = decision.publication_approval_sha256  # type: ignore[union-attr]
    plan_digest = decision.publication_plan_sha256  # type: ignore[union-attr]
    source_operation = decision.source_operation  # type: ignore[union-attr]
    recovery_kind = decision.recovery_kind  # type: ignore[union-attr]
    chosen = decision.decision  # type: ignore[union-attr]
    decided_by = decision.decided_by  # type: ignore[union-attr]
    decision_id = decision.decision_id  # type: ignore[union-attr]
    state = decision.state  # type: ignore[union-attr]

    if type(schema_version) is not str or schema_version != _DECISION_SCHEMA_VERSION:
        _raise_decision("configuration")
    if not _is_sha256(lifecycle_digest):
        _raise_decision("configuration")
    if not _is_sha256(start_digest):
        _raise_decision("configuration")
    if not _is_sha256(approval_digest):
        _raise_decision("configuration")
    if not _is_sha256(plan_digest):
        _raise_decision("configuration")
    if type(source_operation) is not str or source_operation not in _SOURCE_OPERATIONS:
        _raise_decision("configuration")
    if type(recovery_kind) is not str or recovery_kind not in _RECOVERY_KINDS:
        _raise_decision("configuration")
    if recovery_kind == "reconciliation_mismatch" and source_operation != "resume":
        _raise_decision("configuration")
    if type(chosen) is not str or chosen not in _DECISIONS:
        _raise_decision("configuration")
    if type(state) is not str or state not in _STATES:
        _raise_decision("configuration")
    _validate_operator_metadata(decided_by, decision_id)


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
    except ExternalPublicationRecoveryDecisionPersistenceError:
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
    except ExternalPublicationRecoveryDecisionLoadError:
        raise
    except Exception:
        _raise_load("target")


def _write_recovery_decision(path: Path, contents: bytes) -> None:
    try:
        handle = path.open("xb")
    except FileExistsError:
        _verify_existing_decision(path, contents)
        return
    except OSError as error:
        if error.errno == errno.EEXIST:
            _verify_existing_decision(path, contents)
            return
        _raise_persistence("create")
    except Exception:
        _raise_persistence("create")

    _persist_new_decision(handle, path.parent, contents)


def _persist_new_decision(handle: object, directory: Path, contents: bytes) -> None:
    try:
        with _decision_handle_scope(handle) as active_handle:
            written = active_handle.write(contents)  # type: ignore[attr-defined]
            if type(written) is not int or written != len(contents):
                raise OSError("short recovery decision write")
            active_handle.flush()  # type: ignore[attr-defined]
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_decision_directory(directory)
    except Exception:
        _raise_persistence("ambiguous")


def _verify_existing_decision(path: Path, contents: bytes) -> None:
    try:
        if (
            path.is_symlink()
            or not path.exists()
            or path.is_dir()
            or not path.is_file()
        ):
            _raise_persistence("target")
        with path.open("rb") as handle:
            existing = handle.read(_MAX_DECISION_BYTES + 1)
        if type(existing) is not bytes:
            _raise_persistence("target")
    except ExternalPublicationRecoveryDecisionPersistenceError:
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
        with _decision_handle_scope(handle) as active_handle:
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")
    try:
        _fsync_decision_directory(path.parent)
    except Exception:
        _raise_persistence("ambiguous")


@contextmanager
def _decision_handle_scope(handle: object) -> Iterator[object]:
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
            raise OSError("recovery decision handle cannot close")
        close()


def _fsync_decision_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_decision(value: object) -> ExternalPublicationRecoveryDecision:
    if type(value) is not dict or frozenset(value) != _DECISION_KEYS:
        _raise_load("keys")
    try:
        return ExternalPublicationRecoveryDecision(
            schema_version=value["schema_version"],
            lifecycle_outcome_sha256=value["lifecycle_outcome_sha256"],
            operation_start_sha256=value["operation_start_sha256"],
            publication_approval_sha256=value["publication_approval_sha256"],
            publication_plan_sha256=value["publication_plan_sha256"],
            source_operation=value["source_operation"],
            recovery_kind=value["recovery_kind"],
            decision=value["decision"],
            decided_by=value["decided_by"],
            decision_id=value["decision_id"],
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


def _raise_decision(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryDecisionCompatibilityError(
        classification
    ) from None


def _raise_persistence(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryDecisionPersistenceError(classification) from None


def _raise_conflict() -> NoReturn:
    raise ExternalPublicationRecoveryDecisionConflictError("conflict") from None


def _raise_load(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryDecisionLoadError(classification) from None


__all__ = [
    "ExternalPublicationRecoveryDecision",
    "ExternalPublicationRecoveryDecisionCompatibilityError",
    "ExternalPublicationRecoveryDecisionConflictError",
    "ExternalPublicationRecoveryDecisionError",
    "ExternalPublicationRecoveryDecisionFailureDetail",
    "ExternalPublicationRecoveryDecisionLoadError",
    "ExternalPublicationRecoveryDecisionPersistenceError",
    "decide_and_persist_external_publication_recovery",
    "external_publication_recovery_decision_canonical_bytes",
    "external_publication_recovery_decision_digest",
    "load_external_publication_recovery_decision",
    "persist_external_publication_recovery_decision",
    "serialize_external_publication_recovery_decision_canonical",
]
