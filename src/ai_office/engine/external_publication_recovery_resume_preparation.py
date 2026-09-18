"""Durable recovery-authorized resume-lineage preparation evidence.

Phase 294 adds one append-only, immutable preparation boundary on top of the
Phase 293 explicit recovery decision.  It consumes only an exact durable Phase
293 decision whose decision is ``authorize_resume_preparation``, strictly
revalidates the complete Phase 292 lifecycle outcome and Phase 290 start
provenance behind that decision, and durably records that a *future* explicit
phase may prepare a new resume-operation lineage.

Authorization provenance is never inferred from the lifecycle, the start, or
any caller argument.  Only the exact Phase 293 decision carries the explicit
recovery authorization, and its digest is bound into the preparation record.

``stop`` never reaches preparation: it is rejected before any target mutation.

Phase 294 does **not**:

- create a Phase 289 operation intent or a new Phase 290 start marker;
- acquire, execute, reconcile, or replay anything;
- call Phase 293/292/291 orchestration, Phase 290 acquisition, or the Phase
  288/287/285 boundaries;
- execute provider, transport, or network work;
- treat ``prepared`` as execution permission.

``prepared`` means only that the exact Phase 293 ``authorize_resume_preparation``
decision and its exact recovery provenance have been validated and durably bound
to a future target operation of ``resume``.  It does not mean a resume intent
exists, that a resume has started, that Phase 290 acquisition is authorized, or
that a fresh operation may be replayed.
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
from .external_publication_recovery_decision import (
    ExternalPublicationRecoveryDecision,
    ExternalPublicationRecoveryDecisionError,
    external_publication_recovery_decision_digest,
    load_external_publication_recovery_decision,
)

_PREPARATION_ERROR_MESSAGE = (
    "external publication recovery resume preparation is invalid"
)
_PERSISTENCE_ERROR_MESSAGE = (
    "external publication recovery resume preparation persistence failed"
)
_LOAD_ERROR_MESSAGE = (
    "external publication recovery resume preparation could not be loaded"
)
_PREPARATION_SCHEMA_VERSION = "external-publication-recovery-resume-preparation.v1"
_PREPARATION_KEYS = frozenset(
    {
        "lifecycle_outcome_sha256",
        "operation_start_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "recovery_decision_sha256",
        "recovery_kind",
        "schema_version",
        "source_operation",
        "state",
        "target_operation",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_MAX_PREPARATION_BYTES = 4096
_MAX_METADATA_LENGTH = 256
_SOURCE_OPERATIONS = frozenset({"fresh", "resume"})
_RECOVERY_KINDS = frozenset({"already_acquired", "reconciliation_mismatch"})
_TARGET_OPERATIONS = frozenset({"resume"})
_STATES = frozenset({"prepared"})
_DECISIONS = frozenset({"stop", "authorize_resume_preparation"})
_LIFECYCLE_STATES = frozenset({"completed", "recovery_required"})
_RESULT_KINDS = frozenset({"execution_result", "reconciliation", "none"})

Classification = Literal[
    "configuration",
    "path_type",
    "decision_contract",
    "decision_state",
    "decision_digest",
    "lifecycle_contract",
    "lifecycle_state",
    "lifecycle_lineage",
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

DecisionLoader = Callable[[Path], object]
DecisionDigestFunction = Callable[[ExternalPublicationRecoveryDecision], object]
LifecycleLoader = Callable[[Path], object]
LifecycleDigestFunction = Callable[
    [ExternalPublicationOperationLifecycleOutcome], object
]
StartLoader = Callable[[Path], object]
StartDigestFunction = Callable[[ExternalPublicationOperationStart], object]

_KNOWN_PREDECESSOR_ERRORS = (
    ExternalPublicationRecoveryDecisionError,
    ExternalPublicationOperationLifecycleOutcomeError,
    ExternalPublicationOperationStartError,
)


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumePreparationFailureDetail:
    """Detail-safe classification for one resume-preparation failure."""

    classification: Classification


class ExternalPublicationRecoveryResumePreparationError(ValueError):
    """Raised when a resume-preparation record or request is not exact."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_PREPARATION_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumePreparationFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumePreparationCompatibilityError(
    ExternalPublicationRecoveryResumePreparationError
):
    """Raised when a path, decision, or predecessor is incompatible."""


class ExternalPublicationRecoveryResumePreparationPersistenceError(
    ExternalPublicationRecoveryResumePreparationError
):
    """Raised when resume-preparation durability cannot be proven."""

    def __init__(self, classification: Classification = "target") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumePreparationFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumePreparationConflictError(
    ExternalPublicationRecoveryResumePreparationPersistenceError
):
    """Raised when an occupied preparation target is not the exact record."""


class ExternalPublicationRecoveryResumePreparationLoadError(
    ExternalPublicationRecoveryResumePreparationError
):
    """Raised when a resume-preparation sidecar is not an exact record."""

    def __init__(self, classification: Classification = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumePreparationFailureDetail(
            classification
        )


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumePreparation:
    """Immutable, secret-free recovery-authorized resume preparation evidence."""

    schema_version: Literal["external-publication-recovery-resume-preparation.v1"]
    recovery_decision_sha256: str
    lifecycle_outcome_sha256: str
    operation_start_sha256: str
    publication_approval_sha256: str
    publication_plan_sha256: str
    source_operation: Literal["fresh", "resume"]
    recovery_kind: Literal["already_acquired", "reconciliation_mismatch"]
    target_operation: Literal["resume"]
    state: Literal["prepared"]

    def __post_init__(self) -> None:
        _validate_preparation(self)


def serialize_external_publication_recovery_resume_preparation_canonical(
    preparation: ExternalPublicationRecoveryResumePreparation,
) -> str:
    """Serialize one exact resume preparation as compact deterministic JSON."""
    _validate_preparation(preparation)
    try:
        return json.dumps(
            {
                "lifecycle_outcome_sha256": preparation.lifecycle_outcome_sha256,
                "operation_start_sha256": preparation.operation_start_sha256,
                "publication_approval_sha256": (
                    preparation.publication_approval_sha256
                ),
                "publication_plan_sha256": preparation.publication_plan_sha256,
                "recovery_decision_sha256": preparation.recovery_decision_sha256,
                "recovery_kind": preparation.recovery_kind,
                "schema_version": preparation.schema_version,
                "source_operation": preparation.source_operation,
                "state": preparation.state,
                "target_operation": preparation.target_operation,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except ExternalPublicationRecoveryResumePreparationError:
        raise
    except Exception:
        _raise_preparation("serialization")


def external_publication_recovery_resume_preparation_canonical_bytes(
    preparation: ExternalPublicationRecoveryResumePreparation,
) -> bytes:
    """Return exact canonical resume-preparation JSON as UTF-8 bytes."""
    try:
        return serialize_external_publication_recovery_resume_preparation_canonical(
            preparation
        ).encode("utf-8")
    except ExternalPublicationRecoveryResumePreparationError:
        raise
    except UnicodeError:
        _raise_preparation("encoding")
    except Exception:
        _raise_preparation("encoding")


def external_publication_recovery_resume_preparation_digest(
    preparation: ExternalPublicationRecoveryResumePreparation,
) -> str:
    """Return SHA-256 over exact canonical resume-preparation UTF-8 bytes."""
    return sha256(
        external_publication_recovery_resume_preparation_canonical_bytes(preparation)
    ).hexdigest()


def load_external_publication_recovery_resume_preparation(
    path: Path,
) -> ExternalPublicationRecoveryResumePreparation:
    """Read and strictly revalidate one immutable canonical preparation record."""
    _validate_load_path(path)
    try:
        with path.open("rb") as handle:
            contents = handle.read(_MAX_PREPARATION_BYTES + 1)
    except Exception:
        _raise_load("target")
    if type(contents) is not bytes:
        _raise_load("target")
    if len(contents) > _MAX_PREPARATION_BYTES:
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

    preparation = _parse_preparation(value)
    try:
        canonical = external_publication_recovery_resume_preparation_canonical_bytes(
            preparation
        )
    except ExternalPublicationRecoveryResumePreparationError:
        _raise_load("load")
    except Exception:
        _raise_load("load")
    if canonical != contents:
        _raise_load("noncanonical")
    return preparation


def persist_external_publication_recovery_resume_preparation(
    path: Path,
    preparation: ExternalPublicationRecoveryResumePreparation,
) -> None:
    """Durably append-only persist one exact resume-preparation record.

    The canonical bytes are derived and validated first.  A new target is
    created exclusively, written in full, flushed, file-fsynced, closed, and
    then parent-directory-fsynced.  An identical existing target is an
    idempotent success; any other existing content is a fixed conflict and is
    never overwritten, truncated, deleted, or renamed over.  An uncertain
    failure after exclusive creation retains the artifact with no cleanup and
    no retry.
    """
    _validate_persistence_target(path)
    _validate_preparation(preparation)
    try:
        contents = external_publication_recovery_resume_preparation_canonical_bytes(
            preparation
        )
    except ExternalPublicationRecoveryResumePreparationError:
        raise
    except Exception:
        _raise_persistence("serialization")
    _write_preparation(path, contents)


def prepare_and_persist_external_publication_recovery_resume_lineage(
    *,
    recovery_decision_path: Path,
    lifecycle_outcome_path: Path,
    start_path: Path,
    resume_preparation_path: Path,
    decision_loader: DecisionLoader = load_external_publication_recovery_decision,
    decision_digest_function: DecisionDigestFunction = (
        external_publication_recovery_decision_digest
    ),
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
) -> ExternalPublicationRecoveryResumePreparation:
    """Durably record recovery-authorized resume-lineage preparation evidence.

    Phase 294 strict-loads the Phase 293 recovery decision exactly once through
    the caller's exact ``recovery_decision_path`` identity and accepts only an
    exact ``authorize_resume_preparation`` decision.  A ``stop`` decision is
    rejected before any preparation-target mutation.  Authorization is never
    inferred from the lifecycle, the start, or any caller argument.

    The decision digest is computed exactly once from the exact loaded object
    and is bound into the preparation.  The Phase 292 lifecycle outcome is then
    strict-loaded exactly once, must be ``recovery_required``, and its computed
    digest, approval digest, plan digest, operation, and derived recovery kind
    must all match the decision.  The Phase 290 start is strict-loaded exactly
    once and its computed digest, operation, approval digest, and plan digest
    must match both the decision and the lifecycle.

    Only validated durable provenance is used to build the record:
    ``target_operation`` is always exactly ``resume`` and ``state`` is always
    exactly ``prepared``.  No Phase 289 intent is read or created, no Phase 290
    start marker is created, no Phase 291/292/293 orchestration is called, and
    no provider, transport, or network work is performed.

    An existing preparation is strict-loaded once and returned by identity only
    when it equals the exact constructed record; any difference is a fixed
    conflict and leaves the existing bytes unchanged.  Otherwise the
    constructed record is persisted exactly once with no retry.
    """
    _preflight(
        recovery_decision_path=recovery_decision_path,
        lifecycle_outcome_path=lifecycle_outcome_path,
        start_path=start_path,
        resume_preparation_path=resume_preparation_path,
        decision_loader=decision_loader,
        decision_digest_function=decision_digest_function,
        lifecycle_loader=lifecycle_loader,
        lifecycle_digest_function=lifecycle_digest_function,
        start_loader=start_loader,
        start_digest_function=start_digest_function,
    )

    decision = _strict_load_decision(decision_loader, recovery_decision_path)
    _validate_decision_contract(decision)
    _require_authorizing_decision(decision)
    decision_digest = _decision_digest_once(decision_digest_function, decision)

    lifecycle = _strict_load_lifecycle(lifecycle_loader, lifecycle_outcome_path)
    _validate_lifecycle_contract(lifecycle)
    recovery_kind = _derive_recovery_kind(lifecycle)
    lifecycle_digest = _lifecycle_digest_once(lifecycle_digest_function, lifecycle)
    _validate_lifecycle_lineage(
        lifecycle,
        decision=decision,
        lifecycle_digest=lifecycle_digest,
        derived_recovery_kind=recovery_kind,
    )

    start = _strict_load_start(start_loader, start_path)
    _validate_start_contract(start)
    start_digest = _start_digest_once(start_digest_function, start)
    _validate_start_lineage(
        start,
        decision=decision,
        lifecycle=lifecycle,
        start_digest=start_digest,
    )

    constructed = ExternalPublicationRecoveryResumePreparation(
        schema_version=_PREPARATION_SCHEMA_VERSION,  # type: ignore[arg-type]
        recovery_decision_sha256=decision_digest,
        lifecycle_outcome_sha256=lifecycle_digest,
        operation_start_sha256=start_digest,
        publication_approval_sha256=lifecycle.publication_approval_sha256,  # type: ignore[union-attr]
        publication_plan_sha256=lifecycle.publication_plan_sha256,  # type: ignore[union-attr]
        source_operation=lifecycle.operation,  # type: ignore[union-attr]
        recovery_kind=recovery_kind,  # type: ignore[arg-type]
        target_operation="resume",
        state="prepared",
    )

    if _preparation_target_present(resume_preparation_path):
        return _resolve_existing_preparation(resume_preparation_path, constructed)

    persist_external_publication_recovery_resume_preparation(
        resume_preparation_path, constructed
    )
    return constructed


def _preflight(
    *,
    recovery_decision_path: object,
    lifecycle_outcome_path: object,
    start_path: object,
    resume_preparation_path: object,
    decision_loader: object,
    decision_digest_function: object,
    lifecycle_loader: object,
    lifecycle_digest_function: object,
    start_loader: object,
    start_digest_function: object,
) -> None:
    if (
        type(recovery_decision_path) is not _PATH_TYPE
        or type(lifecycle_outcome_path) is not _PATH_TYPE
        or type(start_path) is not _PATH_TYPE
        or type(resume_preparation_path) is not _PATH_TYPE
    ):
        _raise_preparation("path_type")
    if (
        not callable(decision_loader)
        or not callable(decision_digest_function)
        or not callable(lifecycle_loader)
        or not callable(lifecycle_digest_function)
        or not callable(start_loader)
        or not callable(start_digest_function)
    ):
        _raise_preparation("configuration")
    _validate_persistence_target(resume_preparation_path)


def _strict_load_decision(loader: DecisionLoader, decision_path: Path) -> object:
    try:
        decision = loader(decision_path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_preparation("dependency_error")
    if type(decision) is not ExternalPublicationRecoveryDecision:
        _raise_preparation("decision_contract")
    return decision


def _decision_digest_once(
    digest_function: DecisionDigestFunction,
    decision: object,
) -> str:
    try:
        digest = digest_function(decision)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_preparation("dependency_error")
    if not _is_sha256(digest):
        _raise_preparation("decision_digest")
    return digest  # type: ignore[return-value]


def _validate_decision_contract(decision: object) -> None:
    if type(decision) is not ExternalPublicationRecoveryDecision:
        _raise_preparation("decision_contract")
    try:
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
    except ExternalPublicationRecoveryResumePreparationError:
        raise
    except Exception:
        _raise_preparation("decision_contract")

    if (
        type(schema_version) is not str
        or schema_version != "external-publication-recovery-decision.v1"
    ):
        _raise_preparation("decision_contract")
    if not _is_sha256(lifecycle_digest):
        _raise_preparation("decision_contract")
    if not _is_sha256(start_digest):
        _raise_preparation("decision_contract")
    if not _is_sha256(approval_digest):
        _raise_preparation("decision_contract")
    if not _is_sha256(plan_digest):
        _raise_preparation("decision_contract")
    if type(source_operation) is not str or source_operation not in _SOURCE_OPERATIONS:
        _raise_preparation("decision_contract")
    if type(recovery_kind) is not str or recovery_kind not in _RECOVERY_KINDS:
        _raise_preparation("decision_contract")
    if recovery_kind == "reconciliation_mismatch" and source_operation != "resume":
        _raise_preparation("decision_contract")
    if type(chosen) is not str or chosen not in _DECISIONS:
        _raise_preparation("decision_contract")
    if type(state) is not str or state != "decided":
        _raise_preparation("decision_state")
    # Phase 294 revalidates the Phase 293 operator metadata as its own boundary
    # contract instead of relying on the Phase 293 digest helper to reject it.
    _validate_decision_metadata(decided_by, decision_id)


def _validate_decision_metadata(decided_by: object, decision_id: object) -> None:
    for value in (decided_by, decision_id):
        if (
            type(value) is not str
            or not value
            or value != value.strip()
            or len(value) > _MAX_METADATA_LENGTH
        ):
            _raise_preparation("decision_contract")
        if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
            _raise_preparation("decision_contract")


def _require_authorizing_decision(decision: object) -> None:
    chosen = decision.decision  # type: ignore[union-attr]
    if type(chosen) is not str or chosen not in _DECISIONS:
        _raise_preparation("decision_contract")
    if chosen != "authorize_resume_preparation":
        _raise_preparation("decision_state")


def _strict_load_lifecycle(
    loader: LifecycleLoader,
    lifecycle_outcome_path: Path,
) -> object:
    try:
        lifecycle = loader(lifecycle_outcome_path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_preparation("dependency_error")
    if type(lifecycle) is not ExternalPublicationOperationLifecycleOutcome:
        _raise_preparation("lifecycle_contract")
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
        _raise_preparation("dependency_error")
    if not _is_sha256(digest):
        _raise_preparation("lifecycle_digest")
    return digest  # type: ignore[return-value]


def _validate_lifecycle_contract(lifecycle: object) -> None:
    if type(lifecycle) is not ExternalPublicationOperationLifecycleOutcome:
        _raise_preparation("lifecycle_contract")
    try:
        schema_version = lifecycle.schema_version  # type: ignore[union-attr]
        start_digest = lifecycle.operation_start_sha256  # type: ignore[union-attr]
        approval_digest = lifecycle.publication_approval_sha256  # type: ignore[union-attr]
        plan_digest = lifecycle.publication_plan_sha256  # type: ignore[union-attr]
        operation = lifecycle.operation  # type: ignore[union-attr]
        state = lifecycle.state  # type: ignore[union-attr]
        result_kind = lifecycle.result_kind  # type: ignore[union-attr]
        result_digest = lifecycle.result_sha256  # type: ignore[union-attr]
    except ExternalPublicationRecoveryResumePreparationError:
        raise
    except Exception:
        _raise_preparation("lifecycle_contract")

    if (
        type(schema_version) is not str
        or schema_version != "external-publication-operation-lifecycle-outcome.v1"
    ):
        _raise_preparation("lifecycle_contract")
    if not _is_sha256(start_digest):
        _raise_preparation("lifecycle_contract")
    if not _is_sha256(approval_digest):
        _raise_preparation("lifecycle_contract")
    if not _is_sha256(plan_digest):
        _raise_preparation("lifecycle_contract")
    if type(operation) is not str or operation not in _SOURCE_OPERATIONS:
        _raise_preparation("lifecycle_contract")
    if type(state) is not str or state not in _LIFECYCLE_STATES:
        _raise_preparation("lifecycle_contract")
    if type(result_kind) is not str or result_kind not in _RESULT_KINDS:
        _raise_preparation("lifecycle_contract")

    if result_kind == "none":
        if result_digest is not None:
            _raise_preparation("lifecycle_contract")
        if state != "recovery_required":
            _raise_preparation("lifecycle_contract")
        return
    if not _is_sha256(result_digest):
        _raise_preparation("lifecycle_contract")
    if state == "completed":
        if operation == "fresh" and result_kind != "execution_result":
            _raise_preparation("lifecycle_contract")
        if operation == "resume" and result_kind != "reconciliation":
            _raise_preparation("lifecycle_contract")
        return
    if operation != "resume" or result_kind != "reconciliation":
        _raise_preparation("lifecycle_contract")


def _derive_recovery_kind(lifecycle: object) -> str:
    operation = lifecycle.operation  # type: ignore[union-attr]
    state = lifecycle.state  # type: ignore[union-attr]
    result_kind = lifecycle.result_kind  # type: ignore[union-attr]
    result_digest = lifecycle.result_sha256  # type: ignore[union-attr]

    if state == "completed":
        _raise_preparation("lifecycle_state")
    if state != "recovery_required":
        _raise_preparation("lifecycle_state")
    if result_kind == "none":
        if result_digest is not None:
            _raise_preparation("lifecycle_contract")
        return "already_acquired"
    if result_kind == "reconciliation":
        if operation != "resume":
            _raise_preparation("lifecycle_state")
        if not _is_sha256(result_digest):
            _raise_preparation("lifecycle_contract")
        return "reconciliation_mismatch"
    _raise_preparation("lifecycle_state")


def _validate_lifecycle_lineage(
    lifecycle: object,
    *,
    decision: object,
    lifecycle_digest: str,
    derived_recovery_kind: str,
) -> None:
    try:
        state = lifecycle.state  # type: ignore[union-attr]
        operation = lifecycle.operation  # type: ignore[union-attr]
        approval_digest = lifecycle.publication_approval_sha256  # type: ignore[union-attr]
        plan_digest = lifecycle.publication_plan_sha256  # type: ignore[union-attr]
    except Exception:
        _raise_preparation("lifecycle_contract")

    if state == "completed":
        _raise_preparation("lifecycle_state")
    if state != "recovery_required":
        _raise_preparation("lifecycle_state")
    if lifecycle_digest != decision.lifecycle_outcome_sha256:  # type: ignore[union-attr]
        _raise_preparation("lifecycle_digest")
    if operation != decision.source_operation:  # type: ignore[union-attr]
        _raise_preparation("lifecycle_lineage")
    if approval_digest != decision.publication_approval_sha256:  # type: ignore[union-attr]
        _raise_preparation("lifecycle_lineage")
    if plan_digest != decision.publication_plan_sha256:  # type: ignore[union-attr]
        _raise_preparation("lifecycle_lineage")
    if derived_recovery_kind != decision.recovery_kind:  # type: ignore[union-attr]
        _raise_preparation("lifecycle_lineage")


def _strict_load_start(loader: StartLoader, start_path: Path) -> object:
    try:
        start = loader(start_path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_preparation("dependency_error")
    if type(start) is not ExternalPublicationOperationStart:
        _raise_preparation("start_contract")
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
        _raise_preparation("dependency_error")
    if not _is_sha256(digest):
        _raise_preparation("start_lineage")
    return digest  # type: ignore[return-value]


def _validate_start_contract(start: object) -> None:
    if type(start) is not ExternalPublicationOperationStart:
        _raise_preparation("start_contract")
    try:
        if (
            type(start.schema_version) is not str  # type: ignore[union-attr]
            or start.schema_version  # type: ignore[union-attr]
            != "external-publication-operation-start.v1"
        ):
            _raise_preparation("start_contract")
        if not _is_sha256(start.operation_intent_sha256):  # type: ignore[union-attr]
            _raise_preparation("start_contract")
        if not _is_sha256(start.publication_approval_sha256):  # type: ignore[union-attr]
            _raise_preparation("start_contract")
        if not _is_sha256(start.publication_plan_sha256):  # type: ignore[union-attr]
            _raise_preparation("start_contract")
        if (
            type(start.operation) is not str  # type: ignore[union-attr]
            or start.operation not in _SOURCE_OPERATIONS  # type: ignore[union-attr]
        ):
            _raise_preparation("start_contract")
        if (
            type(start.state) is not str  # type: ignore[union-attr]
            or start.state != "started"  # type: ignore[union-attr]
        ):
            _raise_preparation("start_contract")
    except ExternalPublicationRecoveryResumePreparationError:
        raise
    except Exception:
        _raise_preparation("start_contract")


def _validate_start_lineage(
    start: object,
    *,
    decision: object,
    lifecycle: object,
    start_digest: str,
) -> None:
    try:
        operation = start.operation  # type: ignore[union-attr]
        start_approval = start.publication_approval_sha256  # type: ignore[union-attr]
        start_plan = start.publication_plan_sha256  # type: ignore[union-attr]
    except Exception:
        _raise_preparation("start_contract")

    if operation != decision.source_operation:  # type: ignore[union-attr]
        _raise_preparation("start_lineage")
    if operation != lifecycle.operation:  # type: ignore[union-attr]
        _raise_preparation("start_lineage")
    if start_digest != decision.operation_start_sha256:  # type: ignore[union-attr]
        _raise_preparation("start_lineage")
    if start_digest != lifecycle.operation_start_sha256:  # type: ignore[union-attr]
        _raise_preparation("start_lineage")
    if (
        start_approval != decision.publication_approval_sha256  # type: ignore[union-attr]
        or start_approval != lifecycle.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_preparation("start_lineage")
    if (
        start_plan != decision.publication_plan_sha256  # type: ignore[union-attr]
        or start_plan != lifecycle.publication_plan_sha256  # type: ignore[union-attr]
    ):
        _raise_preparation("start_lineage")


def _preparation_target_present(path: Path) -> bool:
    try:
        return path.is_symlink() or path.exists()
    except Exception:
        _raise_load("target")


def _resolve_existing_preparation(
    resume_preparation_path: Path,
    constructed: ExternalPublicationRecoveryResumePreparation,
) -> ExternalPublicationRecoveryResumePreparation:
    loaded = load_external_publication_recovery_resume_preparation(
        resume_preparation_path
    )
    if type(loaded) is not ExternalPublicationRecoveryResumePreparation:
        _raise_preparation("load")
    if loaded != constructed:
        _raise_conflict()
    return loaded


def _validate_preparation(preparation: object) -> None:
    if type(preparation) is not ExternalPublicationRecoveryResumePreparation:
        _raise_preparation("configuration")
    try:
        _check_preparation(preparation)
    except ExternalPublicationRecoveryResumePreparationError:
        raise
    except Exception:
        _raise_preparation("configuration")


def _check_preparation(preparation: object) -> None:
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

    if type(schema_version) is not str or schema_version != _PREPARATION_SCHEMA_VERSION:
        _raise_preparation("configuration")
    if not _is_sha256(decision_digest):
        _raise_preparation("configuration")
    if not _is_sha256(lifecycle_digest):
        _raise_preparation("configuration")
    if not _is_sha256(start_digest):
        _raise_preparation("configuration")
    if not _is_sha256(approval_digest):
        _raise_preparation("configuration")
    if not _is_sha256(plan_digest):
        _raise_preparation("configuration")
    if type(source_operation) is not str or source_operation not in _SOURCE_OPERATIONS:
        _raise_preparation("configuration")
    if type(recovery_kind) is not str or recovery_kind not in _RECOVERY_KINDS:
        _raise_preparation("configuration")
    if recovery_kind == "reconciliation_mismatch" and source_operation != "resume":
        _raise_preparation("configuration")
    if type(target_operation) is not str or target_operation not in _TARGET_OPERATIONS:
        _raise_preparation("configuration")
    if type(state) is not str or state not in _STATES:
        _raise_preparation("configuration")


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
    except ExternalPublicationRecoveryResumePreparationPersistenceError:
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
    except ExternalPublicationRecoveryResumePreparationLoadError:
        raise
    except Exception:
        _raise_load("target")


def _write_preparation(path: Path, contents: bytes) -> None:
    try:
        handle = path.open("xb")
    except FileExistsError:
        _verify_existing_preparation(path, contents)
        return
    except OSError as error:
        if error.errno == errno.EEXIST:
            _verify_existing_preparation(path, contents)
            return
        _raise_persistence("create")
    except Exception:
        _raise_persistence("create")

    _persist_new_preparation(handle, path.parent, contents)


def _persist_new_preparation(handle: object, directory: Path, contents: bytes) -> None:
    try:
        with _preparation_handle_scope(handle) as active_handle:
            written = active_handle.write(contents)  # type: ignore[attr-defined]
            if type(written) is not int or written != len(contents):
                raise OSError("short resume preparation write")
            active_handle.flush()  # type: ignore[attr-defined]
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_preparation_directory(directory)
    except Exception:
        _raise_persistence("ambiguous")


def _verify_existing_preparation(path: Path, contents: bytes) -> None:
    try:
        if (
            path.is_symlink()
            or not path.exists()
            or path.is_dir()
            or not path.is_file()
        ):
            _raise_persistence("target")
        with path.open("rb") as handle:
            existing = handle.read(_MAX_PREPARATION_BYTES + 1)
        if type(existing) is not bytes:
            _raise_persistence("target")
    except ExternalPublicationRecoveryResumePreparationPersistenceError:
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
        with _preparation_handle_scope(handle) as active_handle:
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")
    try:
        _fsync_preparation_directory(path.parent)
    except Exception:
        _raise_persistence("ambiguous")


@contextmanager
def _preparation_handle_scope(handle: object) -> Iterator[object]:
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
            raise OSError("resume preparation handle cannot close")
        close()


def _fsync_preparation_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_preparation(
    value: object,
) -> ExternalPublicationRecoveryResumePreparation:
    if type(value) is not dict or frozenset(value) != _PREPARATION_KEYS:
        _raise_load("keys")
    try:
        return ExternalPublicationRecoveryResumePreparation(
            schema_version=value["schema_version"],
            recovery_decision_sha256=value["recovery_decision_sha256"],
            lifecycle_outcome_sha256=value["lifecycle_outcome_sha256"],
            operation_start_sha256=value["operation_start_sha256"],
            publication_approval_sha256=value["publication_approval_sha256"],
            publication_plan_sha256=value["publication_plan_sha256"],
            source_operation=value["source_operation"],
            recovery_kind=value["recovery_kind"],
            target_operation=value["target_operation"],
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


def _raise_preparation(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumePreparationCompatibilityError(
        classification
    ) from None


def _raise_persistence(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumePreparationPersistenceError(
        classification
    ) from None


def _raise_conflict() -> NoReturn:
    raise ExternalPublicationRecoveryResumePreparationConflictError(
        "conflict"
    ) from None


def _raise_load(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumePreparationLoadError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationRecoveryResumePreparation",
    "ExternalPublicationRecoveryResumePreparationCompatibilityError",
    "ExternalPublicationRecoveryResumePreparationConflictError",
    "ExternalPublicationRecoveryResumePreparationError",
    "ExternalPublicationRecoveryResumePreparationFailureDetail",
    "ExternalPublicationRecoveryResumePreparationLoadError",
    "ExternalPublicationRecoveryResumePreparationPersistenceError",
    "external_publication_recovery_resume_preparation_canonical_bytes",
    "external_publication_recovery_resume_preparation_digest",
    "load_external_publication_recovery_resume_preparation",
    "persist_external_publication_recovery_resume_preparation",
    "prepare_and_persist_external_publication_recovery_resume_lineage",
    "serialize_external_publication_recovery_resume_preparation_canonical",
]
