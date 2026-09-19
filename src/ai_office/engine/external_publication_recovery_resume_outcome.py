"""Durable recovery resume outcome evidence (Phase 298).

Phase 298 adds one append-only, immutable **recovery resume outcome** bound to
the exact Phase 296 recovery resume start authorization lineage.  It consumes
the exact Phase 295 binding, the exact Phase 296 authorization, the exact bound
Phase 289 ``resume`` intent, and one exact runtime resume request, derives the
canonical Phase 290 start target and the canonical Phase 298 outcome target
internally, uses an existing exact outcome as a zero-Phase297 idempotent fast
path, and otherwise calls the existing public Phase 297 handoff exactly once.

The outcome preserves the recovery-specific Phase 296 authorization identity so
that a generic Phase 292 lifecycle record, which carries no such provenance, is
never sufficient as the canonical Phase 298 artifact.  Phase 298 classifies
only the normal Phase 297 return, strict-loads and binds the exact durable
Phase 290 start marker, and records:

    completed / reconciliation / digest          -- Phase 297 reported matched
    recovery_required / reconciliation / digest  -- Phase 297 reported mismatch
    recovery_required / none / None              -- Phase 297 stopped already_acquired

``already_acquired`` is uncertainty, never evidence of an earlier successful
completion.  Phase 298 never reconstructs ``acquired``, never infers a lost
reconciliation result from the existence of a start marker, never replays an
already-acquired operation, and adds no retry, cleanup, or continuation.
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

from .external_publication import (
    ExternalPublicationApproval,
    ExternalPublicationError,
    external_publication_approval_digest,
)
from .external_publication_execution import (
    ExternalPublicationExecutionError,
)
from .external_publication_execution_evidence import (
    ExternalPublicationExecutionEvidenceError,
)
from .external_publication_execution_orchestration import (
    ExternalPublicationExecutionOrchestrationError,
)
from .external_publication_execution_reconciliation import (
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationExecutionReconciliationError,
)
from .external_publication_execution_reconciliation_evidence import (  # noqa: E501
    ExternalPublicationExecutionReconciliationEvidenceError,
    external_publication_execution_reconciliation_digest,
)
from .external_publication_execution_reconciliation_orchestration import (  # noqa: E501
    ExternalPublicationExecutionReconciliationOrchestrationError,
)
from .external_publication_execution_reconciliation_resume import (  # noqa: E501
    ExternalPublicationExecutionReconciliationResumeError,
)
from .external_publication_operation import (
    ExternalPublicationFreshOperationRequest,
    ExternalPublicationOperationError,
    ExternalPublicationResumeOperationRequest,
)
from .external_publication_operation_intent import (
    ExternalPublicationOperationIntent,
    ExternalPublicationOperationIntentError,
    external_publication_operation_intent_digest,
    load_external_publication_operation_intent,
)
from .external_publication_operation_start import (
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartAcquisition,
    ExternalPublicationOperationStartError,
    external_publication_operation_start_digest,
    load_external_publication_operation_start,
)
from .external_publication_operation_start_handoff import (
    ExternalPublicationOperationStartHandoffError,
)
from .external_publication_recovery_resume_intent_binding import (  # noqa: E501
    ExternalPublicationRecoveryResumeIntentBinding,
    ExternalPublicationRecoveryResumeIntentBindingError,
    external_publication_recovery_resume_intent_binding_digest,
    load_external_publication_recovery_resume_intent_binding,
)
from .external_publication_recovery_resume_start_authorization import (  # noqa: E501
    ExternalPublicationRecoveryResumeStartAuthorization,
    ExternalPublicationRecoveryResumeStartAuthorizationError,
    external_publication_recovery_resume_start_authorization_digest,
    load_external_publication_recovery_resume_start_authorization,
)
from .external_publication_recovery_resume_start_handoff import (  # noqa: E501
    ExternalPublicationRecoveryResumeStartHandoffError,
    run_external_publication_recovery_resume_start_handoff,
)

_INVALID_ERROR_MESSAGE = "external publication recovery resume outcome is invalid"
_PERSISTENCE_ERROR_MESSAGE = (
    "external publication recovery resume outcome persistence failed"
)
_LOAD_ERROR_MESSAGE = "external publication recovery resume outcome could not be loaded"
_OUTCOME_SCHEMA_VERSION = "external-publication-recovery-resume-outcome.v1"
_BINDING_SCHEMA_VERSION = "external-publication-recovery-resume-intent-binding.v1"
_AUTHORIZATION_SCHEMA_VERSION = (
    "external-publication-recovery-resume-start-authorization.v1"
)
_INTENT_SCHEMA_VERSION = "external-publication-operation-intent.v1"
_START_SCHEMA_VERSION = "external-publication-operation-start.v1"
_OUTCOME_KEYS = frozenset(
    {
        "operation",
        "operation_intent_sha256",
        "operation_start_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "recovery_kind",
        "result_kind",
        "result_sha256",
        "resume_intent_binding_sha256",
        "resume_start_authorization_sha256",
        "schema_version",
        "source_operation",
        "state",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_MAX_OUTCOME_BYTES = 4096
_MAX_APPROVAL_METADATA_LENGTH = 256
_SOURCE_OPERATIONS = frozenset({"fresh", "resume"})
_RECOVERY_KINDS = frozenset({"already_acquired", "reconciliation_mismatch"})
_BINDING_OPERATIONS = frozenset({"resume"})
_BINDING_STATES = frozenset({"authorized"})
_AUTHORIZATION_OPERATIONS = frozenset({"resume"})
_AUTHORIZATION_STATES = frozenset({"authorized"})
_INTENT_OPERATIONS = frozenset({"resume"})
_START_STATES = frozenset({"started"})
_OUTCOME_OPERATIONS = frozenset({"resume"})
_OUTCOME_STATES = frozenset({"completed", "recovery_required"})
_RESULT_KINDS = frozenset({"reconciliation", "none"})
_ACQUISITION_STOP_STATUS = "already_acquired"
_RECONCILIATION_MATCHED = "matched"
_RECONCILIATION_MISMATCH = "lineage_mismatch"
_AUTHORIZATION_FILENAME_PREFIX = (
    "external-publication-recovery-resume-start-authorization-"
)
_START_FILENAME_PREFIX = "external-publication-recovery-resume-start-"
_OUTCOME_FILENAME_PREFIX = "external-publication-recovery-resume-outcome-"
_FILENAME_SUFFIX = ".json"
_REQUEST_PATH_FIELDS = (
    "ledger_directory",
    "execution_evidence_path",
    "execution_reconciliation_evidence_path",
)

Classification = Literal[
    "configuration",
    "path_type",
    "path_conflict",
    "request_contract",
    "request_lineage",
    "binding_contract",
    "binding_digest",
    "authorization_contract",
    "authorization_digest",
    "authorization_lineage",
    "intent_contract",
    "intent_digest",
    "intent_lineage",
    "start_contract",
    "start_digest",
    "result_contract",
    "result_digest",
    "outcome_contract",
    "outcome_lineage",
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

BindingLoader = Callable[[Path], object]
BindingDigestFunction = Callable[
    [ExternalPublicationRecoveryResumeIntentBinding], object
]
AuthorizationLoader = Callable[[Path], object]
AuthorizationDigestFunction = Callable[
    [ExternalPublicationRecoveryResumeStartAuthorization], object
]
IntentLoader = Callable[[Path], object]
IntentDigestFunction = Callable[[ExternalPublicationOperationIntent], object]
ApprovalDigestFunction = Callable[[ExternalPublicationApproval], object]
StartDigestFunction = Callable[[ExternalPublicationOperationStart], object]
StartLoader = Callable[[Path], object]
ReconciliationDigestFunction = Callable[
    [ExternalPublicationExecutionReconciliation], object
]
Phase297Function = Callable[..., object]

_KNOWN_PREDECESSOR_ERRORS = (
    ExternalPublicationRecoveryResumeIntentBindingError,
    ExternalPublicationRecoveryResumeStartAuthorizationError,
    ExternalPublicationOperationIntentError,
    ExternalPublicationOperationStartError,
    ExternalPublicationRecoveryResumeStartHandoffError,
    ExternalPublicationOperationStartHandoffError,
    ExternalPublicationOperationError,
    ExternalPublicationError,
    ExternalPublicationExecutionOrchestrationError,
    ExternalPublicationExecutionError,
    ExternalPublicationExecutionEvidenceError,
    ExternalPublicationExecutionReconciliationResumeError,
    ExternalPublicationExecutionReconciliationOrchestrationError,
    ExternalPublicationExecutionReconciliationError,
    ExternalPublicationExecutionReconciliationEvidenceError,
)

_RECONCILIATION_STATUSES = frozenset(
    {_RECONCILIATION_MATCHED, _RECONCILIATION_MISMATCH}
)


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeOutcomeFailureDetail:
    """Detail-safe classification for one recovery resume outcome failure."""

    classification: Classification


class ExternalPublicationRecoveryResumeOutcomeError(ValueError):
    """Raised when a recovery resume outcome or request is not exact."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_INVALID_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeOutcomeFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeOutcomeCompatibilityError(
    ExternalPublicationRecoveryResumeOutcomeError
):
    """Raised when a path, model, lineage, or result is incompatible."""


class ExternalPublicationRecoveryResumeOutcomePersistenceError(
    ExternalPublicationRecoveryResumeOutcomeError
):
    """Raised when recovery-resume-outcome durability cannot be proven."""

    def __init__(self, classification: Classification = "target") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeOutcomeFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeOutcomeConflictError(
    ExternalPublicationRecoveryResumeOutcomePersistenceError
):
    """Raised when an occupied outcome target is not the exact record."""


class ExternalPublicationRecoveryResumeOutcomeLoadError(
    ExternalPublicationRecoveryResumeOutcomeError
):
    """Raised when a recovery-resume-outcome sidecar is not an exact record."""

    def __init__(self, classification: Classification = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeOutcomeFailureDetail(
            classification
        )


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeOutcome:
    """Immutable, secret-free durable recovery resume outcome evidence.

    ``completed`` records only that the exact same-invocation Phase 297 handoff
    returned one exact reconciliation with status ``matched``.  ``recovery_required``
    records that it returned a mismatched reconciliation or stopped with
    ``already_acquired``.  The record carries the exact Phase 296 authorization
    identity, the exact Phase 295 binding identity, the exact bound Phase 289
    intent identity, and the exact durable Phase 290 start-marker digest.
    """

    schema_version: Literal["external-publication-recovery-resume-outcome.v1"]
    resume_start_authorization_sha256: str
    resume_intent_binding_sha256: str
    operation_intent_sha256: str
    operation_start_sha256: str
    publication_approval_sha256: str
    publication_plan_sha256: str
    source_operation: Literal["fresh", "resume"]
    recovery_kind: Literal["already_acquired", "reconciliation_mismatch"]
    operation: Literal["resume"]
    state: Literal["completed", "recovery_required"]
    result_kind: Literal["reconciliation", "none"]
    result_sha256: str | None

    def __post_init__(self) -> None:
        _validate_outcome(self)


def serialize_external_publication_recovery_resume_outcome_canonical(
    outcome: ExternalPublicationRecoveryResumeOutcome,
) -> str:
    """Serialize one exact recovery resume outcome as compact JSON."""
    _validate_outcome(outcome)
    try:
        return json.dumps(
            {
                "operation": outcome.operation,
                "operation_intent_sha256": outcome.operation_intent_sha256,
                "operation_start_sha256": outcome.operation_start_sha256,
                "publication_approval_sha256": (outcome.publication_approval_sha256),
                "publication_plan_sha256": outcome.publication_plan_sha256,
                "recovery_kind": outcome.recovery_kind,
                "result_kind": outcome.result_kind,
                "result_sha256": outcome.result_sha256,
                "resume_intent_binding_sha256": (outcome.resume_intent_binding_sha256),
                "resume_start_authorization_sha256": (
                    outcome.resume_start_authorization_sha256
                ),
                "schema_version": outcome.schema_version,
                "source_operation": outcome.source_operation,
                "state": outcome.state,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except ExternalPublicationRecoveryResumeOutcomeError:
        raise
    except Exception:
        _raise_outcome("serialization")


def external_publication_recovery_resume_outcome_canonical_bytes(
    outcome: ExternalPublicationRecoveryResumeOutcome,
) -> bytes:
    """Return exact canonical recovery-resume-outcome JSON as UTF-8 bytes."""
    try:
        canonical = serialize_external_publication_recovery_resume_outcome_canonical(
            outcome
        )
        return canonical.encode("utf-8")
    except ExternalPublicationRecoveryResumeOutcomeError:
        raise
    except UnicodeError:
        _raise_outcome("encoding")
    except Exception:
        _raise_outcome("encoding")


def external_publication_recovery_resume_outcome_digest(
    outcome: ExternalPublicationRecoveryResumeOutcome,
) -> str:
    """Return SHA-256 over exact canonical recovery-resume-outcome UTF-8 bytes."""
    return sha256(
        external_publication_recovery_resume_outcome_canonical_bytes(outcome)
    ).hexdigest()


def load_external_publication_recovery_resume_outcome(
    path: Path,
) -> ExternalPublicationRecoveryResumeOutcome:
    """Read and strictly revalidate one immutable canonical outcome record."""
    _validate_load_path(path)
    try:
        with path.open("rb") as handle:
            contents = handle.read(_MAX_OUTCOME_BYTES + 1)
    except Exception:
        _raise_load("target")
    if type(contents) is not bytes:
        _raise_load("target")
    if len(contents) > _MAX_OUTCOME_BYTES:
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

    outcome = _parse_outcome(value)
    try:
        canonical = external_publication_recovery_resume_outcome_canonical_bytes(
            outcome
        )
    except ExternalPublicationRecoveryResumeOutcomeError:
        _raise_load("load")
    except Exception:
        _raise_load("load")
    if canonical != contents:
        _raise_load("noncanonical")
    return outcome


def persist_external_publication_recovery_resume_outcome(
    path: Path,
    outcome: ExternalPublicationRecoveryResumeOutcome,
) -> None:
    """Durably append-only persist one exact recovery-resume-outcome record.

    The canonical bytes are derived and validated first.  A new target is
    created exclusively, written in full, flushed, file-fsynced, closed, and
    then parent-directory-fsynced.  An identical existing target is an
    idempotent durable success; any other existing content is a fixed conflict
    and is never overwritten, truncated, deleted, or renamed over.  Any
    uncertainty after exclusive creation retains the artifact with no cleanup,
    no retry, and no rewrite.
    """
    _validate_persistence_target(path)
    _validate_outcome(outcome)
    try:
        contents = external_publication_recovery_resume_outcome_canonical_bytes(outcome)
    except ExternalPublicationRecoveryResumeOutcomeError:
        raise
    except Exception:
        _raise_persistence("serialization")
    _write_outcome(path, contents)


def run_and_persist_external_publication_recovery_resume_outcome(
    *,
    resume_intent_binding_path: Path,
    resume_intent_path: Path,
    request: ExternalPublicationResumeOperationRequest,
    binding_loader: BindingLoader = (
        load_external_publication_recovery_resume_intent_binding
    ),
    binding_digest_function: BindingDigestFunction = (
        external_publication_recovery_resume_intent_binding_digest
    ),
    authorization_loader: AuthorizationLoader = (
        load_external_publication_recovery_resume_start_authorization
    ),
    authorization_digest_function: AuthorizationDigestFunction = (
        external_publication_recovery_resume_start_authorization_digest
    ),
    intent_loader: IntentLoader = load_external_publication_operation_intent,
    intent_digest_function: IntentDigestFunction = (
        external_publication_operation_intent_digest
    ),
    approval_digest_function: ApprovalDigestFunction = (
        external_publication_approval_digest
    ),
    expected_start_digest_function: StartDigestFunction = (
        external_publication_operation_start_digest
    ),
    start_loader: StartLoader = load_external_publication_operation_start,
    start_digest_function: StartDigestFunction = (
        external_publication_operation_start_digest
    ),
    reconciliation_digest_function: ReconciliationDigestFunction = (
        external_publication_execution_reconciliation_digest
    ),
    phase297_function: Phase297Function = (
        run_external_publication_recovery_resume_start_handoff
    ),
) -> ExternalPublicationRecoveryResumeOutcome:
    """Persist one durable recovery resume outcome for one authorization.

    The exact Phase 295 binding, exact Phase 296 authorization, exact bound
    Phase 289 resume intent, exact in-memory expected Phase 290 start identity,
    and exact runtime resume request lineage are all validated before any
    irreversible work.  The canonical Phase 290 start path and the canonical
    Phase 298 outcome path are derived internally from the exact binding path
    parent and the exact computed authorization digest; the caller supplies
    neither.

    An existing exact outcome is a zero-Phase297 idempotent fast path that
    revalidates the durable start marker and the complete provenance lineage and
    then returns the loaded object by identity.  Otherwise the public Phase 297
    handoff is called exactly once with only the caller binding path, the caller
    intent path, and the caller request; the durable start marker is then
    strict-loaded and bound, the normal Phase 297 return is classified, and the
    outcome is persisted exactly once with no retry.
    """
    _preflight(
        resume_intent_binding_path=resume_intent_binding_path,
        resume_intent_path=resume_intent_path,
        request=request,
        binding_loader=binding_loader,
        binding_digest_function=binding_digest_function,
        authorization_loader=authorization_loader,
        authorization_digest_function=authorization_digest_function,
        intent_loader=intent_loader,
        intent_digest_function=intent_digest_function,
        approval_digest_function=approval_digest_function,
        expected_start_digest_function=expected_start_digest_function,
        start_loader=start_loader,
        start_digest_function=start_digest_function,
        reconciliation_digest_function=reconciliation_digest_function,
        phase297_function=phase297_function,
    )

    binding = _strict_load_binding(binding_loader, resume_intent_binding_path)
    _validate_binding_contract(binding)
    binding_digest = _binding_digest_once(binding_digest_function, binding)

    authorization_path = _derive_authorization_path(
        resume_intent_binding_path, binding_digest
    )
    authorization = _strict_load_authorization(authorization_loader, authorization_path)
    _validate_authorization_contract(authorization)
    authorization_digest = _authorization_digest_once(
        authorization_digest_function, authorization
    )
    _validate_authorization_lineage(authorization, binding, binding_digest)

    intent = _strict_load_intent(intent_loader, resume_intent_path)
    _validate_intent_contract(intent)
    intent_digest = _intent_digest_once(intent_digest_function, intent)
    _validate_intent_lineage(intent, binding, authorization, intent_digest)

    expected_start = _construct_expected_start(intent, intent_digest)
    _validate_start_contract(expected_start)
    expected_start_digest = _expected_start_digest_once(
        expected_start_digest_function, expected_start
    )
    if expected_start_digest != authorization.expected_operation_start_sha256:  # type: ignore[union-attr]
        _raise_outcome("start_digest")

    _validate_request_lineage(
        request=request,
        binding=binding,
        authorization=authorization,
        intent=intent,
        approval_digest_function=approval_digest_function,
    )

    start_path = _derive_start_path(resume_intent_binding_path, authorization_digest)
    outcome_path = _derive_outcome_path(
        resume_intent_binding_path, authorization_digest
    )
    _validate_future_target(outcome_path)

    if _target_present(outcome_path):
        return _resolve_existing_outcome(
            outcome_path=outcome_path,
            start_path=start_path,
            binding=binding,
            authorization=authorization,
            intent=intent,
            binding_digest=binding_digest,
            authorization_digest=authorization_digest,
            intent_digest=intent_digest,
            start_loader=start_loader,
            start_digest_function=start_digest_function,
        )

    result = _call_phase297(
        phase297_function=phase297_function,
        resume_intent_binding_path=resume_intent_binding_path,
        resume_intent_path=resume_intent_path,
        request=request,
    )

    start, start_digest = _strict_load_and_bind_start(
        start_loader=start_loader,
        start_digest_function=start_digest_function,
        start_path=start_path,
        authorization=authorization,
    )

    state, result_kind, result_sha256 = _classify_result(
        result,
        start=start,
        authorization=authorization,
        reconciliation_digest_function=reconciliation_digest_function,
    )

    outcome = _construct_outcome(
        authorization_digest=authorization_digest,
        binding_digest=binding_digest,
        intent_digest=intent_digest,
        start_digest=start_digest,
        authorization=authorization,
        state=state,
        result_kind=result_kind,
        result_sha256=result_sha256,
    )

    persist_external_publication_recovery_resume_outcome(outcome_path, outcome)
    return outcome


def _preflight(
    *,
    resume_intent_binding_path: object,
    resume_intent_path: object,
    request: object,
    binding_loader: object,
    binding_digest_function: object,
    authorization_loader: object,
    authorization_digest_function: object,
    intent_loader: object,
    intent_digest_function: object,
    approval_digest_function: object,
    expected_start_digest_function: object,
    start_loader: object,
    start_digest_function: object,
    reconciliation_digest_function: object,
    phase297_function: object,
) -> None:
    """Reject every invalid input before any load, digest, or mutation."""
    if (
        type(resume_intent_binding_path) is not _PATH_TYPE
        or type(resume_intent_path) is not _PATH_TYPE
    ):
        _raise_outcome("path_type")
    if resume_intent_binding_path == resume_intent_path:
        _raise_outcome("path_conflict")

    if type(request) is ExternalPublicationFreshOperationRequest:
        _raise_outcome("request_contract")
    if type(request) is not ExternalPublicationResumeOperationRequest:
        _raise_outcome("request_contract")

    if not (
        callable(binding_loader)
        and callable(binding_digest_function)
        and callable(authorization_loader)
        and callable(authorization_digest_function)
        and callable(intent_loader)
        and callable(intent_digest_function)
        and callable(approval_digest_function)
        and callable(expected_start_digest_function)
        and callable(start_loader)
        and callable(start_digest_function)
        and callable(reconciliation_digest_function)
        and callable(phase297_function)
    ):
        _raise_outcome("configuration")

    for field_name in _REQUEST_PATH_FIELDS:
        if type(getattr(request, field_name, None)) is not _PATH_TYPE:
            _raise_outcome("path_type")
    if hasattr(request, "transport") or hasattr(request, "provider"):
        _raise_outcome("request_contract")

    approval = request.approval  # type: ignore[union-attr]
    if type(approval) is not ExternalPublicationApproval:
        _raise_outcome("request_contract")
    try:
        approved = approval.approved
        plan_digest = approval.publication_plan_sha256
        approved_by = approval.approved_by
        approval_id = approval.approval_id
    except Exception:
        _raise_outcome("request_contract")
    if type(approved) is not bool or approved is not True:
        _raise_outcome("request_contract")
    if not _is_sha256(plan_digest):
        _raise_outcome("request_contract")
    if not _is_valid_metadata(approved_by) or not _is_valid_metadata(approval_id):
        _raise_outcome("request_contract")


def _strict_load_binding(loader: BindingLoader, binding_path: Path) -> object:
    try:
        binding = loader(binding_path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    if type(binding) is not ExternalPublicationRecoveryResumeIntentBinding:
        _raise_outcome("binding_contract")
    return binding


def _binding_digest_once(
    digest_function: BindingDigestFunction, binding: object
) -> str:
    try:
        digest = digest_function(binding)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    if not _is_sha256(digest):
        _raise_outcome("binding_digest")
    return digest  # type: ignore[return-value]


def _derive_authorization_path(
    resume_intent_binding_path: Path, binding_digest: str
) -> Path:
    """Derive the only allowed Phase 296 authorization path for one binding."""
    return resume_intent_binding_path.parent / (
        f"{_AUTHORIZATION_FILENAME_PREFIX}{binding_digest}{_FILENAME_SUFFIX}"
    )


def _derive_start_path(
    resume_intent_binding_path: Path, authorization_digest: str
) -> Path:
    """Derive the one canonical Phase 290 start target for one authorization."""
    return resume_intent_binding_path.parent / (
        f"{_START_FILENAME_PREFIX}{authorization_digest}{_FILENAME_SUFFIX}"
    )


def _derive_outcome_path(
    resume_intent_binding_path: Path, authorization_digest: str
) -> Path:
    """Derive the one canonical Phase 298 outcome target for one authorization."""
    return resume_intent_binding_path.parent / (
        f"{_OUTCOME_FILENAME_PREFIX}{authorization_digest}{_FILENAME_SUFFIX}"
    )


def _strict_load_authorization(
    loader: AuthorizationLoader, authorization_path: Path
) -> object:
    try:
        authorization = loader(authorization_path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    if type(authorization) is not ExternalPublicationRecoveryResumeStartAuthorization:
        _raise_outcome("authorization_contract")
    return authorization


def _authorization_digest_once(
    digest_function: AuthorizationDigestFunction, authorization: object
) -> str:
    try:
        digest = digest_function(authorization)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    if not _is_sha256(digest):
        _raise_outcome("authorization_digest")
    return digest  # type: ignore[return-value]


def _strict_load_intent(loader: IntentLoader, intent_path: Path) -> object:
    try:
        intent = loader(intent_path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    if type(intent) is not ExternalPublicationOperationIntent:
        _raise_outcome("intent_contract")
    return intent


def _intent_digest_once(digest_function: IntentDigestFunction, intent: object) -> str:
    try:
        digest = digest_function(intent)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    if not _is_sha256(digest):
        _raise_outcome("intent_digest")
    return digest  # type: ignore[return-value]


def _expected_start_digest_once(
    digest_function: StartDigestFunction, start: object
) -> str:
    try:
        digest = digest_function(start)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    if not _is_sha256(digest):
        _raise_outcome("start_digest")
    return digest  # type: ignore[return-value]


def _validate_binding_contract(binding: object) -> None:
    if type(binding) is not ExternalPublicationRecoveryResumeIntentBinding:
        _raise_outcome("binding_contract")
    try:
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
    except ExternalPublicationRecoveryResumeOutcomeError:
        raise
    except Exception:
        _raise_outcome("binding_contract")

    if type(schema_version) is not str or schema_version != _BINDING_SCHEMA_VERSION:
        _raise_outcome("binding_contract")
    if not _is_sha256(preparation_digest):
        _raise_outcome("binding_contract")
    if not _is_sha256(decision_digest):
        _raise_outcome("binding_contract")
    if not _is_sha256(approval_digest):
        _raise_outcome("binding_contract")
    if not _is_sha256(plan_digest):
        _raise_outcome("binding_contract")
    if not _is_sha256(intent_digest):
        _raise_outcome("binding_contract")
    if type(source_operation) is not str or source_operation not in _SOURCE_OPERATIONS:
        _raise_outcome("binding_contract")
    if type(recovery_kind) is not str or recovery_kind not in _RECOVERY_KINDS:
        _raise_outcome("binding_contract")
    if recovery_kind == "reconciliation_mismatch" and source_operation != "resume":
        _raise_outcome("binding_contract")
    if type(operation) is not str or operation not in _BINDING_OPERATIONS:
        _raise_outcome("binding_contract")
    if type(state) is not str or state not in _BINDING_STATES:
        _raise_outcome("binding_contract")


def _validate_authorization_contract(authorization: object) -> None:
    if type(authorization) is not ExternalPublicationRecoveryResumeStartAuthorization:
        _raise_outcome("authorization_contract")
    try:
        schema_version = authorization.schema_version  # type: ignore[union-attr]
        binding_digest = authorization.resume_intent_binding_sha256  # type: ignore[union-attr]
        preparation_digest = authorization.resume_preparation_sha256  # type: ignore[union-attr]
        decision_digest = authorization.recovery_decision_sha256  # type: ignore[union-attr]
        intent_digest = authorization.operation_intent_sha256  # type: ignore[union-attr]
        expected_start_digest = (  # type: ignore[union-attr]
            authorization.expected_operation_start_sha256
        )
        approval_digest = authorization.publication_approval_sha256  # type: ignore[union-attr]
        plan_digest = authorization.publication_plan_sha256  # type: ignore[union-attr]
        source_operation = authorization.source_operation  # type: ignore[union-attr]
        recovery_kind = authorization.recovery_kind  # type: ignore[union-attr]
        operation = authorization.operation  # type: ignore[union-attr]
        state = authorization.state  # type: ignore[union-attr]
    except ExternalPublicationRecoveryResumeOutcomeError:
        raise
    except Exception:
        _raise_outcome("authorization_contract")

    if (
        type(schema_version) is not str
        or schema_version != _AUTHORIZATION_SCHEMA_VERSION
    ):
        _raise_outcome("authorization_contract")
    if not _is_sha256(binding_digest):
        _raise_outcome("authorization_contract")
    if not _is_sha256(preparation_digest):
        _raise_outcome("authorization_contract")
    if not _is_sha256(decision_digest):
        _raise_outcome("authorization_contract")
    if not _is_sha256(intent_digest):
        _raise_outcome("authorization_contract")
    if not _is_sha256(expected_start_digest):
        _raise_outcome("authorization_contract")
    if not _is_sha256(approval_digest):
        _raise_outcome("authorization_contract")
    if not _is_sha256(plan_digest):
        _raise_outcome("authorization_contract")
    if type(source_operation) is not str or source_operation not in _SOURCE_OPERATIONS:
        _raise_outcome("authorization_contract")
    if type(recovery_kind) is not str or recovery_kind not in _RECOVERY_KINDS:
        _raise_outcome("authorization_contract")
    if recovery_kind == "reconciliation_mismatch" and source_operation != "resume":
        _raise_outcome("authorization_contract")
    if type(operation) is not str or operation not in _AUTHORIZATION_OPERATIONS:
        _raise_outcome("authorization_contract")
    if type(state) is not str or state not in _AUTHORIZATION_STATES:
        _raise_outcome("authorization_contract")


def _validate_authorization_lineage(
    authorization: object, binding: object, binding_digest: str
) -> None:
    if (
        authorization.resume_intent_binding_sha256 != binding_digest  # type: ignore[union-attr]
    ):
        _raise_outcome("authorization_lineage")
    if (
        authorization.resume_preparation_sha256  # type: ignore[union-attr]
        != binding.resume_preparation_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("authorization_lineage")
    if (
        authorization.recovery_decision_sha256  # type: ignore[union-attr]
        != binding.recovery_decision_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("authorization_lineage")
    if (
        authorization.operation_intent_sha256  # type: ignore[union-attr]
        != binding.operation_intent_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("authorization_lineage")
    if (
        authorization.publication_approval_sha256  # type: ignore[union-attr]
        != binding.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("authorization_lineage")
    if (
        authorization.publication_plan_sha256  # type: ignore[union-attr]
        != binding.publication_plan_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("authorization_lineage")
    if (
        authorization.source_operation  # type: ignore[union-attr]
        != binding.source_operation  # type: ignore[union-attr]
    ):
        _raise_outcome("authorization_lineage")
    if (
        authorization.recovery_kind  # type: ignore[union-attr]
        != binding.recovery_kind  # type: ignore[union-attr]
    ):
        _raise_outcome("authorization_lineage")
    if authorization.operation != binding.operation:  # type: ignore[union-attr]
        _raise_outcome("authorization_lineage")
    if authorization.operation != "resume":  # type: ignore[union-attr]
        _raise_outcome("authorization_lineage")
    if authorization.state != "authorized":  # type: ignore[union-attr]
        _raise_outcome("authorization_lineage")


def _validate_intent_contract(intent: object) -> None:
    if type(intent) is not ExternalPublicationOperationIntent:
        _raise_outcome("intent_contract")
    try:
        schema_version = intent.schema_version  # type: ignore[union-attr]
        approval_digest = intent.publication_approval_sha256  # type: ignore[union-attr]
        plan_digest = intent.publication_plan_sha256  # type: ignore[union-attr]
        operation = intent.operation  # type: ignore[union-attr]
    except ExternalPublicationRecoveryResumeOutcomeError:
        raise
    except Exception:
        _raise_outcome("intent_contract")

    if type(schema_version) is not str or schema_version != _INTENT_SCHEMA_VERSION:
        _raise_outcome("intent_contract")
    if not _is_sha256(approval_digest):
        _raise_outcome("intent_contract")
    if not _is_sha256(plan_digest):
        _raise_outcome("intent_contract")
    if type(operation) is not str or operation not in _INTENT_OPERATIONS:
        _raise_outcome("intent_contract")


def _validate_intent_lineage(
    intent: object,
    binding: object,
    authorization: ExternalPublicationRecoveryResumeStartAuthorization,
    intent_digest: str,
) -> None:
    if intent_digest != binding.operation_intent_sha256:  # type: ignore[union-attr]
        _raise_outcome("intent_lineage")
    if intent_digest != authorization.operation_intent_sha256:  # type: ignore[union-attr]
        _raise_outcome("intent_lineage")
    if (
        intent.publication_approval_sha256  # type: ignore[union-attr]
        != binding.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("intent_lineage")
    if (
        intent.publication_approval_sha256  # type: ignore[union-attr]
        != authorization.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("intent_lineage")
    if (
        intent.publication_plan_sha256  # type: ignore[union-attr]
        != binding.publication_plan_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("intent_lineage")
    if (
        intent.publication_plan_sha256  # type: ignore[union-attr]
        != authorization.publication_plan_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("intent_lineage")
    if intent.operation != binding.operation:  # type: ignore[union-attr]
        _raise_outcome("intent_lineage")
    if intent.operation != authorization.operation:  # type: ignore[union-attr]
        _raise_outcome("intent_lineage")


def _construct_expected_start(
    intent: object, intent_digest: str
) -> ExternalPublicationOperationStart:
    try:
        return ExternalPublicationOperationStart(
            schema_version=_START_SCHEMA_VERSION,  # type: ignore[arg-type]
            operation_intent_sha256=intent_digest,
            publication_approval_sha256=intent.publication_approval_sha256,  # type: ignore[union-attr]
            publication_plan_sha256=intent.publication_plan_sha256,  # type: ignore[union-attr]
            operation="resume",
            state="started",
        )
    except ExternalPublicationRecoveryResumeOutcomeError:
        raise
    except Exception:
        _raise_outcome("start_contract")


def _validate_start_contract(
    start: object, classification: Classification = "start_contract"
) -> None:
    if type(start) is not ExternalPublicationOperationStart:
        _raise_outcome(classification)
    try:
        schema_version = start.schema_version  # type: ignore[union-attr]
        intent_digest = start.operation_intent_sha256  # type: ignore[union-attr]
        approval_digest = start.publication_approval_sha256  # type: ignore[union-attr]
        plan_digest = start.publication_plan_sha256  # type: ignore[union-attr]
        operation = start.operation  # type: ignore[union-attr]
        state = start.state  # type: ignore[union-attr]
    except ExternalPublicationRecoveryResumeOutcomeError:
        raise
    except Exception:
        _raise_outcome(classification)

    if type(schema_version) is not str or schema_version != _START_SCHEMA_VERSION:
        _raise_outcome(classification)
    if not _is_sha256(intent_digest):
        _raise_outcome(classification)
    if not _is_sha256(approval_digest):
        _raise_outcome(classification)
    if not _is_sha256(plan_digest):
        _raise_outcome(classification)
    if type(operation) is not str or operation != "resume":
        _raise_outcome(classification)
    if type(state) is not str or state not in _START_STATES:
        _raise_outcome(classification)


def _validate_request_lineage(
    *,
    request: ExternalPublicationResumeOperationRequest,
    binding: object,
    authorization: ExternalPublicationRecoveryResumeStartAuthorization,
    intent: object,
    approval_digest_function: ApprovalDigestFunction,
) -> str:
    try:
        approval_digest = approval_digest_function(request.approval)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    if not _is_sha256(approval_digest):
        _raise_outcome("request_lineage")
    if (
        approval_digest != authorization.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("request_lineage")
    if approval_digest != binding.publication_approval_sha256:  # type: ignore[union-attr]
        _raise_outcome("request_lineage")

    plan_digest = request.approval.publication_plan_sha256
    if plan_digest != authorization.publication_plan_sha256:  # type: ignore[union-attr]
        _raise_outcome("request_lineage")
    if plan_digest != binding.publication_plan_sha256:  # type: ignore[union-attr]
        _raise_outcome("request_lineage")
    if plan_digest != intent.publication_plan_sha256:  # type: ignore[union-attr]
        _raise_outcome("request_lineage")
    return approval_digest  # type: ignore[return-value]


def _validate_future_target(path: Path) -> None:
    """Validate an occupied outcome target shape before any Phase 297 call."""
    try:
        if not path.parent.exists() or not path.parent.is_dir():
            _raise_outcome("parent")
        if path.is_symlink() or path.is_dir() or (path.exists() and not path.is_file()):
            _raise_outcome("target")
    except ExternalPublicationRecoveryResumeOutcomeError:
        raise
    except Exception:
        _raise_outcome("target")


def _target_present(path: Path) -> bool:
    try:
        return path.is_symlink() or path.exists()
    except Exception:
        _raise_outcome("load")


def _resolve_existing_outcome(
    *,
    outcome_path: Path,
    start_path: Path,
    binding: object,
    authorization: ExternalPublicationRecoveryResumeStartAuthorization,
    intent: object,
    binding_digest: str,
    authorization_digest: str,
    intent_digest: str,
    start_loader: StartLoader,
    start_digest_function: StartDigestFunction,
) -> ExternalPublicationRecoveryResumeOutcome:
    """Validate and return an existing exact outcome with zero Phase 297 calls.

    The durable start marker is still strict-loaded and digested, and the
    complete provenance lineage is rechecked, so an existing outcome is never
    trusted on its own.  The reconciliation digest helper is deliberately not
    called here because the stored digest is already part of the exact record.
    """
    outcome = load_external_publication_recovery_resume_outcome(outcome_path)

    if (
        outcome.resume_start_authorization_sha256 != authorization_digest
        or outcome.resume_intent_binding_sha256 != binding_digest
        or outcome.operation_intent_sha256 != intent_digest
    ):
        _raise_outcome("outcome_lineage")
    if (
        outcome.publication_approval_sha256 != authorization.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("outcome_lineage")
    if (
        outcome.publication_plan_sha256 != authorization.publication_plan_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("outcome_lineage")
    if outcome.source_operation != authorization.source_operation:  # type: ignore[union-attr]
        _raise_outcome("outcome_lineage")
    if outcome.recovery_kind != authorization.recovery_kind:  # type: ignore[union-attr]
        _raise_outcome("outcome_lineage")
    if outcome.operation != authorization.operation:  # type: ignore[union-attr]
        _raise_outcome("outcome_lineage")

    _durable_start, start_digest = _strict_load_and_bind_start(
        start_loader=start_loader,
        start_digest_function=start_digest_function,
        start_path=start_path,
        authorization=authorization,
    )
    if start_digest != outcome.operation_start_sha256:
        _raise_outcome("outcome_lineage")
    if outcome.operation_intent_sha256 != intent_digest:
        _raise_outcome("outcome_lineage")
    if (
        outcome.publication_approval_sha256 != intent.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("outcome_lineage")
    if (
        outcome.publication_plan_sha256 != intent.publication_plan_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("outcome_lineage")
    return outcome


def _strict_load_and_bind_start(
    *,
    start_loader: StartLoader,
    start_digest_function: StartDigestFunction,
    start_path: Path,
    authorization: ExternalPublicationRecoveryResumeStartAuthorization,
) -> tuple[ExternalPublicationOperationStart, str]:
    """Strict-load the durable Phase 290 marker and bind it to the authorization."""
    try:
        start = start_loader(start_path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    _validate_start_contract(start)
    start_digest = _expected_start_digest_once(start_digest_function, start)
    if (
        start_digest != authorization.expected_operation_start_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("start_digest")
    if start.operation_intent_sha256 != authorization.operation_intent_sha256:  # type: ignore[union-attr]
        _raise_outcome("start_contract")
    if (
        start.publication_approval_sha256  # type: ignore[union-attr]
        != authorization.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("start_contract")
    if (
        start.publication_plan_sha256  # type: ignore[union-attr]
        != authorization.publication_plan_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("start_contract")
    if start.operation != authorization.operation:  # type: ignore[union-attr]
        _raise_outcome("start_contract")
    return start, start_digest


def _call_phase297(
    *,
    phase297_function: Phase297Function,
    resume_intent_binding_path: Path,
    resume_intent_path: Path,
    request: ExternalPublicationResumeOperationRequest,
) -> object:
    """Call the public Phase 297 handoff exactly once with exact identities."""
    try:
        return phase297_function(
            resume_intent_binding_path=resume_intent_binding_path,
            resume_intent_path=resume_intent_path,
            request=request,
        )
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")


def _classify_result(
    result: object,
    *,
    start: ExternalPublicationOperationStart,
    authorization: ExternalPublicationRecoveryResumeStartAuthorization,
    reconciliation_digest_function: ReconciliationDigestFunction,
) -> tuple[str, str, str | None]:
    if type(result) is ExternalPublicationOperationStartAcquisition:
        return _classify_acquisition(result, start=start, authorization=authorization)
    if type(result) is ExternalPublicationExecutionReconciliation:
        return _classify_reconciliation(
            result, reconciliation_digest_function=reconciliation_digest_function
        )
    _raise_outcome("result_contract")


def _classify_acquisition(
    acquisition: object,
    *,
    start: ExternalPublicationOperationStart,
    authorization: object,
) -> tuple[str, str, str | None]:
    """Classify the exact already_acquired stop result as uncertainty."""
    status = acquisition.status  # type: ignore[union-attr]
    if type(status) is not str or status != _ACQUISITION_STOP_STATUS:
        _raise_outcome("result_contract")
    embedded = acquisition.start  # type: ignore[union-attr]
    _validate_start_contract(embedded, "result_contract")
    if (
        embedded.operation_intent_sha256  # type: ignore[union-attr]
        != authorization.operation_intent_sha256  # type: ignore[union-attr]
    ):
        _raise_outcome("result_contract")
    if embedded != start:  # type: ignore[operator]
        _raise_outcome("result_contract")
    return "recovery_required", "none", None


def _classify_reconciliation(
    reconciliation: object,
    *,
    reconciliation_digest_function: ReconciliationDigestFunction,
) -> tuple[str, str, str | None]:
    _revalidate_reconciliation(reconciliation)
    try:
        digest = reconciliation_digest_function(reconciliation)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    if not _is_sha256(digest):
        _raise_outcome("result_digest")
    status = reconciliation.status  # type: ignore[union-attr]
    if status == _RECONCILIATION_MATCHED:
        return "completed", "reconciliation", digest  # type: ignore[return-value]
    return "recovery_required", "reconciliation", digest  # type: ignore[return-value]


def _revalidate_reconciliation(reconciliation: object) -> None:
    """Revalidate the exact public Phase 283 contract before trusting a digest.

    The exact Phase 297-returned object is reconstructed through the public
    model so that every Phase 283 cross-field invariant - allowed lineage field
    names, no duplicates, canonical lineage-field order, and the exact
    ``matched``/``lineage_mismatch`` coupling with ``mismatched_fields`` - is
    enforced locally instead of being delegated to the injected digest helper.
    The reconstruction is validation only; the original object is never
    replaced and is the identity later handed to the digest helper.
    """
    if type(reconciliation) is not ExternalPublicationExecutionReconciliation:
        _raise_outcome("result_contract")
    try:
        ExternalPublicationExecutionReconciliation(
            schema_version=reconciliation.schema_version,  # type: ignore[union-attr]
            claim_sha256=reconciliation.claim_sha256,  # type: ignore[union-attr]
            execution_evidence_sha256=(  # type: ignore[union-attr]
                reconciliation.execution_evidence_sha256
            ),
            status=reconciliation.status,  # type: ignore[union-attr]
            mismatched_fields=(  # type: ignore[union-attr]
                reconciliation.mismatched_fields
            ),
        )
    except ExternalPublicationRecoveryResumeOutcomeError:
        raise
    except Exception:
        _raise_outcome("result_contract")


def _construct_outcome(
    *,
    authorization_digest: str,
    binding_digest: str,
    intent_digest: str,
    start_digest: str,
    authorization: ExternalPublicationRecoveryResumeStartAuthorization,
    state: str,
    result_kind: str,
    result_sha256: str | None,
) -> ExternalPublicationRecoveryResumeOutcome:
    try:
        return ExternalPublicationRecoveryResumeOutcome(
            schema_version=_OUTCOME_SCHEMA_VERSION,  # type: ignore[arg-type]
            resume_start_authorization_sha256=authorization_digest,
            resume_intent_binding_sha256=binding_digest,
            operation_intent_sha256=intent_digest,
            operation_start_sha256=start_digest,
            publication_approval_sha256=authorization.publication_approval_sha256,  # type: ignore[union-attr]
            publication_plan_sha256=authorization.publication_plan_sha256,  # type: ignore[union-attr]
            source_operation=authorization.source_operation,  # type: ignore[union-attr]
            recovery_kind=authorization.recovery_kind,  # type: ignore[union-attr]
            operation="resume",
            state=state,  # type: ignore[arg-type]
            result_kind=result_kind,  # type: ignore[arg-type]
            result_sha256=result_sha256,
        )
    except ExternalPublicationRecoveryResumeOutcomeError:
        raise
    except Exception:
        _raise_outcome("outcome_contract")


def _validate_outcome(outcome: object) -> None:
    if type(outcome) is not ExternalPublicationRecoveryResumeOutcome:
        _raise_outcome("configuration")
    try:
        schema_version = outcome.schema_version  # type: ignore[union-attr]
        authorization_digest = (  # type: ignore[union-attr]
            outcome.resume_start_authorization_sha256
        )
        binding_digest = outcome.resume_intent_binding_sha256  # type: ignore[union-attr]
        intent_digest = outcome.operation_intent_sha256  # type: ignore[union-attr]
        start_digest = outcome.operation_start_sha256  # type: ignore[union-attr]
        approval_digest = outcome.publication_approval_sha256  # type: ignore[union-attr]
        plan_digest = outcome.publication_plan_sha256  # type: ignore[union-attr]
        source_operation = outcome.source_operation  # type: ignore[union-attr]
        recovery_kind = outcome.recovery_kind  # type: ignore[union-attr]
        operation = outcome.operation  # type: ignore[union-attr]
        state = outcome.state  # type: ignore[union-attr]
        result_kind = outcome.result_kind  # type: ignore[union-attr]
        result_digest = outcome.result_sha256  # type: ignore[union-attr]
    except ExternalPublicationRecoveryResumeOutcomeError:
        raise
    except Exception:
        _raise_outcome("configuration")

    if type(schema_version) is not str or schema_version != _OUTCOME_SCHEMA_VERSION:
        _raise_outcome("configuration")
    if not _is_sha256(authorization_digest):
        _raise_outcome("configuration")
    if not _is_sha256(binding_digest):
        _raise_outcome("configuration")
    if not _is_sha256(intent_digest):
        _raise_outcome("configuration")
    if not _is_sha256(start_digest):
        _raise_outcome("configuration")
    if not _is_sha256(approval_digest):
        _raise_outcome("configuration")
    if not _is_sha256(plan_digest):
        _raise_outcome("configuration")
    if type(source_operation) is not str or source_operation not in _SOURCE_OPERATIONS:
        _raise_outcome("configuration")
    if type(recovery_kind) is not str or recovery_kind not in _RECOVERY_KINDS:
        _raise_outcome("configuration")
    if recovery_kind == "reconciliation_mismatch" and source_operation != "resume":
        _raise_outcome("configuration")
    if type(operation) is not str or operation not in _OUTCOME_OPERATIONS:
        _raise_outcome("configuration")
    if type(state) is not str or state not in _OUTCOME_STATES:
        _raise_outcome("configuration")
    if type(result_kind) is not str or result_kind not in _RESULT_KINDS:
        _raise_outcome("configuration")

    if result_kind == "none":
        if result_digest is not None:
            _raise_outcome("configuration")
        if state != "recovery_required":
            _raise_outcome("configuration")
        return
    if not _is_sha256(result_digest):
        _raise_outcome("configuration")


def _write_outcome(path: Path, contents: bytes) -> None:
    try:
        handle = path.open("xb")
    except FileExistsError:
        _verify_existing_outcome(path, contents)
        return
    except OSError as error:
        if error.errno == errno.EEXIST:
            _verify_existing_outcome(path, contents)
            return
        _raise_persistence("create")
    except Exception:
        _raise_persistence("create")

    _persist_new_outcome(handle, path.parent, contents)


def _persist_new_outcome(handle: object, directory: Path, contents: bytes) -> None:
    try:
        with _outcome_handle_scope(handle) as active_handle:
            written = active_handle.write(contents)  # type: ignore[attr-defined]
            if type(written) is not int or written != len(contents):
                raise OSError("short recovery resume outcome write")
            active_handle.flush()  # type: ignore[attr-defined]
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_outcome_directory(directory)
    except Exception:
        _raise_persistence("ambiguous")


def _verify_existing_outcome(path: Path, contents: bytes) -> None:
    try:
        if (
            path.is_symlink()
            or not path.exists()
            or path.is_dir()
            or not path.is_file()
        ):
            _raise_persistence("target")
        with path.open("rb") as handle:
            existing = handle.read(_MAX_OUTCOME_BYTES + 1)
        if type(existing) is not bytes:
            _raise_persistence("target")
    except ExternalPublicationRecoveryResumeOutcomePersistenceError:
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
        with _outcome_handle_scope(handle) as active_handle:
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")
    try:
        _fsync_outcome_directory(path.parent)
    except Exception:
        _raise_persistence("ambiguous")


@contextmanager
def _outcome_handle_scope(handle: object) -> Iterator[object]:
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
            raise OSError("recovery resume outcome handle cannot close")
        close()


def _fsync_outcome_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_outcome(value: object) -> ExternalPublicationRecoveryResumeOutcome:
    if type(value) is not dict:
        _raise_load("parse")
    if set(value) != _OUTCOME_KEYS:
        _raise_load("keys")
    try:
        outcome = ExternalPublicationRecoveryResumeOutcome(
            schema_version=value["schema_version"],  # type: ignore[arg-type]
            resume_start_authorization_sha256=value[  # type: ignore[arg-type]
                "resume_start_authorization_sha256"
            ],
            resume_intent_binding_sha256=value[  # type: ignore[arg-type]
                "resume_intent_binding_sha256"
            ],
            operation_intent_sha256=value["operation_intent_sha256"],  # type: ignore[arg-type]
            operation_start_sha256=value["operation_start_sha256"],  # type: ignore[arg-type]
            publication_approval_sha256=value[  # type: ignore[arg-type]
                "publication_approval_sha256"
            ],
            publication_plan_sha256=value["publication_plan_sha256"],  # type: ignore[arg-type]
            source_operation=value["source_operation"],  # type: ignore[arg-type]
            recovery_kind=value["recovery_kind"],  # type: ignore[arg-type]
            operation=value["operation"],  # type: ignore[arg-type]
            state=value["state"],  # type: ignore[arg-type]
            result_kind=value["result_kind"],  # type: ignore[arg-type]
            result_sha256=value["result_sha256"],  # type: ignore[arg-type]
        )
    except ExternalPublicationRecoveryResumeOutcomeError:
        _raise_load("load")
    except Exception:
        _raise_load("parse")
    return outcome


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


class _DuplicateKeyError(ValueError):
    """Raised when a canonical outcome payload repeats a key."""


class _NonStandardJSONConstantError(ValueError):
    """Raised when a canonical outcome payload uses a non-standard constant."""


def _reject_nonstandard_json_constant(value: str) -> NoReturn:
    raise _NonStandardJSONConstantError(value)


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
    except ExternalPublicationRecoveryResumeOutcomePersistenceError:
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
    except ExternalPublicationRecoveryResumeOutcomeLoadError:
        raise
    except Exception:
        _raise_load("target")


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _is_valid_metadata(value: object) -> bool:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > _MAX_APPROVAL_METADATA_LENGTH
    ):
        return False
    return all(
        unicodedata.category(character) not in {"Cc", "Cs"} for character in value
    )


def _raise_outcome(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeOutcomeCompatibilityError(
        classification
    ) from None


def _raise_persistence(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeOutcomePersistenceError(
        classification
    ) from None


def _raise_conflict() -> NoReturn:
    raise ExternalPublicationRecoveryResumeOutcomeConflictError("conflict") from None


def _raise_load(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeOutcomeLoadError(classification) from None


__all__ = [
    "ExternalPublicationRecoveryResumeOutcome",
    "ExternalPublicationRecoveryResumeOutcomeCompatibilityError",
    "ExternalPublicationRecoveryResumeOutcomeConflictError",
    "ExternalPublicationRecoveryResumeOutcomeError",
    "ExternalPublicationRecoveryResumeOutcomeFailureDetail",
    "ExternalPublicationRecoveryResumeOutcomeLoadError",
    "ExternalPublicationRecoveryResumeOutcomePersistenceError",
    "external_publication_recovery_resume_outcome_canonical_bytes",
    "external_publication_recovery_resume_outcome_digest",
    "load_external_publication_recovery_resume_outcome",
    "persist_external_publication_recovery_resume_outcome",
    "run_and_persist_external_publication_recovery_resume_outcome",
    "serialize_external_publication_recovery_resume_outcome_canonical",
]
