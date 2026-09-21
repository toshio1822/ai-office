# ruff: noqa: E501

"""Crash-recoverable Phase 307 reconciliation outcome boundary.

Phase 307 consumes one exact Phase 303 start authorization and one exact
resume request.  It lets the public Phase 306 boundary run at most once when
this authorization has no Phase 307 outcome, then classifies only the
authorization-specific durable reconciliation evidence.  The result is one
append-only, immutable outcome; it never creates or repairs a start marker,
creates reconciliation evidence, retries a lower phase, or performs external
publication work.
"""

from __future__ import annotations

import errno
import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn

from .external_publication import (
    ExternalPublicationApproval,
    ExternalPublicationError,
    external_publication_approval_digest,
    external_publication_attempt_claim_path,
    external_publication_consumption_key,
)
from .external_publication_execution_reconciliation import (
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationExecutionReconciliationError,
    reconcile_external_publication_execution,
)
from .external_publication_execution_reconciliation_evidence import (
    ExternalPublicationExecutionReconciliationEvidenceError,
    external_publication_execution_reconciliation_digest,
    load_external_publication_execution_reconciliation,
)
from .external_publication_execution_reconciliation_orchestration import (
    ExternalPublicationExecutionReconciliationOrchestrationError,
)
from .external_publication_execution_reconciliation_resume import (
    ExternalPublicationExecutionReconciliationResumeError,
)
from .external_publication_operation import (
    ExternalPublicationOperationError,
    ExternalPublicationResumeOperationRequest,
)
from .external_publication_operation_intent import (
    ExternalPublicationOperationIntentError,
)
from .external_publication_operation_start import (
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartAcquisition,
    ExternalPublicationOperationStartError,
    external_publication_operation_start_digest,
    load_external_publication_operation_start,
)
from .external_publication_recovery_resume_decision_preparation_reconciliation_handoff import (
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffError,
    run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff,
)
from .external_publication_recovery_resume_decision_preparation_start_acquisition_handoff import (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffError,
)
from .external_publication_recovery_resume_decision_preparation_start_acquisition_routing import (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingError,
)
from .external_publication_recovery_resume_decision_preparation_start_authorization import (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError,
    external_publication_recovery_resume_decision_preparation_start_authorization_digest,
    load_external_publication_recovery_resume_decision_preparation_start_authorization,
)

_OUTCOME_ERROR_MESSAGE = (
    "external publication recovery resume decision preparation reconciliation "
    "outcome is invalid"
)
_PERSISTENCE_ERROR_MESSAGE = (
    "external publication recovery resume decision preparation reconciliation "
    "outcome persistence failed"
)
_LOAD_ERROR_MESSAGE = (
    "external publication recovery resume decision preparation reconciliation "
    "outcome could not be loaded"
)
_OUTCOME_SCHEMA_VERSION = "external-publication-recovery-resume-decision-preparation-reconciliation-outcome.v1"
_OUTCOME_KEYS = frozenset(
    {
        "schema_version",
        "decision_preparation_start_authorization_sha256",
        "decision_preparation_intent_binding_sha256",
        "decision_preparation_sha256",
        "recovery_resume_decision_sha256",
        "operation_intent_sha256",
        "operation_start_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "source_operation",
        "previous_recovery_kind",
        "recovery_kind",
        "operation",
        "state",
        "result_kind",
        "result_sha256",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_MAX_OUTCOME_BYTES = 4096
_SOURCE_OPERATIONS = frozenset({"fresh", "resume"})
_RECOVERY_KINDS = frozenset({"already_acquired", "reconciliation_mismatch"})
_OUTCOME_STATES = frozenset({"completed", "recovery_required"})
_RESULT_KINDS = frozenset({"reconciliation", "none"})
_RECONCILIATION_STATUSES = frozenset({"matched", "lineage_mismatch"})
_AUTHORIZATION_FILENAME_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-start-authorization-"
)
_START_FILENAME_PREFIX = "external-publication-recovery-resume-start-"
_RECONCILIATION_FILENAME_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-reconciliation-"
)
_OUTCOME_FILENAME_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-reconciliation-outcome-"
)
_FILENAME_SUFFIX = ".json"

Classification = Literal[
    "configuration",
    "path_type",
    "request_contract",
    "authorization_contract",
    "authorization_digest",
    "authorization_path",
    "request_lineage",
    "reconciliation_path",
    "start_contract",
    "start_digest",
    "start_lineage",
    "phase306_contract",
    "reconciliation_contract",
    "reconciliation_digest",
    "reconciliation_lineage",
    "reconciliation_presence",
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
    "size",
    "keys",
    "noncanonical",
    "dependency_error",
]

AuthorizationLoader = Callable[[Path], object]
AuthorizationDigestFunction = Callable[
    [ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization], object
]
ApprovalDigestFunction = Callable[[ExternalPublicationApproval], object]
StartLoader = Callable[[Path], object]
StartDigestFunction = Callable[[ExternalPublicationOperationStart], object]
ReconciliationLoader = Callable[[Path], object]
ReconciliationDigestFunction = Callable[
    [ExternalPublicationExecutionReconciliation], object
]
ConsumptionKeyFunction = Callable[[ExternalPublicationApproval], object]
ClaimPathFunction = Callable[[Path, object], object]
ReconcileFunction = Callable[..., object]
Phase306Function = Callable[..., object]

_KNOWN_AUTHORIZATION_ERRORS = (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError,
)
_KNOWN_START_ERRORS = (ExternalPublicationOperationStartError,)
_KNOWN_RECONCILIATION_ERRORS = (
    ExternalPublicationExecutionReconciliationError,
    ExternalPublicationExecutionReconciliationEvidenceError,
    ExternalPublicationExecutionReconciliationOrchestrationError,
    ExternalPublicationExecutionReconciliationResumeError,
)
_KNOWN_PHASE306_ERRORS = (
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError,
    ExternalPublicationOperationIntentError,
    ExternalPublicationOperationStartError,
    ExternalPublicationOperationError,
    ExternalPublicationError,
    ExternalPublicationExecutionReconciliationResumeError,
    ExternalPublicationExecutionReconciliationOrchestrationError,
    ExternalPublicationExecutionReconciliationError,
    ExternalPublicationExecutionReconciliationEvidenceError,
)


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeFailureDetail:
    """Detail-safe classification for one Phase 307 failure."""

    classification: Classification


class ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError(
    ValueError
):
    """Raised when a Phase 307 input, result, or artifact is not exact."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_OUTCOME_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeCompatibilityError(
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
):
    """Raised when a Phase 307 contract or lineage is incompatible."""


class ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomePersistenceError(
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
):
    """Raised when Phase 307 outcome durability cannot be proven."""

    def __init__(self, classification: Classification = "target") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeConflictError(
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomePersistenceError
):
    """Raised when durable Phase 307 state contains a conflicting artifact."""


class ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeLoadError(
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
):
    """Raised when an outcome artifact is not exact canonical evidence."""

    def __init__(self, classification: Classification = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeFailureDetail(
            classification
        )


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome:
    """Immutable, secret-free current-lineage Phase 307 outcome."""

    schema_version: Literal[
        "external-publication-recovery-resume-decision-preparation-reconciliation-outcome.v1"
    ]
    decision_preparation_start_authorization_sha256: str
    decision_preparation_intent_binding_sha256: str
    decision_preparation_sha256: str
    recovery_resume_decision_sha256: str
    operation_intent_sha256: str
    operation_start_sha256: str
    publication_approval_sha256: str
    publication_plan_sha256: str
    source_operation: Literal["fresh", "resume"]
    previous_recovery_kind: Literal["already_acquired", "reconciliation_mismatch"]
    recovery_kind: Literal["already_acquired", "reconciliation_mismatch"]
    operation: Literal["resume"]
    state: Literal["completed", "recovery_required"]
    result_kind: Literal["reconciliation", "none"]
    result_sha256: str | None

    def __post_init__(self) -> None:
        _validate_outcome(self)


def serialize_external_publication_recovery_resume_decision_preparation_reconciliation_outcome_canonical(
    outcome: ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome,
) -> str:
    """Serialize one exact Phase 307 outcome as compact canonical JSON."""
    _validate_outcome(outcome)
    try:
        return json.dumps(
            {
                "decision_preparation_intent_binding_sha256": (
                    outcome.decision_preparation_intent_binding_sha256
                ),
                "decision_preparation_sha256": outcome.decision_preparation_sha256,
                "decision_preparation_start_authorization_sha256": (
                    outcome.decision_preparation_start_authorization_sha256
                ),
                "operation": outcome.operation,
                "operation_intent_sha256": outcome.operation_intent_sha256,
                "operation_start_sha256": outcome.operation_start_sha256,
                "previous_recovery_kind": outcome.previous_recovery_kind,
                "publication_approval_sha256": outcome.publication_approval_sha256,
                "publication_plan_sha256": outcome.publication_plan_sha256,
                "recovery_kind": outcome.recovery_kind,
                "recovery_resume_decision_sha256": (
                    outcome.recovery_resume_decision_sha256
                ),
                "result_kind": outcome.result_kind,
                "result_sha256": outcome.result_sha256,
                "schema_version": outcome.schema_version,
                "source_operation": outcome.source_operation,
                "state": outcome.state,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        raise
    except Exception:
        _raise_outcome("serialization")


def external_publication_recovery_resume_decision_preparation_reconciliation_outcome_canonical_bytes(
    outcome: ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome,
) -> bytes:
    """Return exact canonical Phase 307 JSON encoded as UTF-8 bytes."""
    try:
        return serialize_external_publication_recovery_resume_decision_preparation_reconciliation_outcome_canonical(
            outcome
        ).encode("utf-8")
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        raise
    except UnicodeError:
        _raise_outcome("encoding")
    except Exception:
        _raise_outcome("encoding")


def external_publication_recovery_resume_decision_preparation_reconciliation_outcome_digest(
    outcome: ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome,
) -> str:
    """Return SHA-256 over exact canonical Phase 307 UTF-8 bytes."""
    try:
        return sha256(
            external_publication_recovery_resume_decision_preparation_reconciliation_outcome_canonical_bytes(
                outcome
            )
        ).hexdigest()
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        raise
    except Exception:
        _raise_outcome("encoding")


def load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
    path: Path,
) -> ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome:
    """Strict-load one exact canonical Phase 307 outcome artifact."""
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
        canonical = external_publication_recovery_resume_decision_preparation_reconciliation_outcome_canonical_bytes(
            outcome
        )
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        _raise_load("load")
    except Exception:
        _raise_load("load")
    if canonical != contents:
        _raise_load("noncanonical")
    return outcome


def persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
    path: Path,
    outcome: ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome,
) -> None:
    """Append-only durably persist one exact Phase 307 outcome."""
    _validate_outcome(outcome)
    try:
        contents = external_publication_recovery_resume_decision_preparation_reconciliation_outcome_canonical_bytes(
            outcome
        )
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        raise
    except Exception:
        _raise_persistence("serialization")
    _validate_persistence_target(path)
    _write_outcome(path, contents)


def run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
    *,
    start_authorization_path: Path,
    request: ExternalPublicationResumeOperationRequest,
    authorization_loader: AuthorizationLoader = load_external_publication_recovery_resume_decision_preparation_start_authorization,
    authorization_digest_function: AuthorizationDigestFunction = external_publication_recovery_resume_decision_preparation_start_authorization_digest,
    approval_digest_function: ApprovalDigestFunction = external_publication_approval_digest,
    start_loader: StartLoader = load_external_publication_operation_start,
    start_digest_function: StartDigestFunction = external_publication_operation_start_digest,
    reconciliation_loader: ReconciliationLoader = load_external_publication_execution_reconciliation,
    reconciliation_digest_function: ReconciliationDigestFunction = external_publication_execution_reconciliation_digest,
    phase280_consumption_key_function: ConsumptionKeyFunction = external_publication_consumption_key,
    phase280_claim_path_function: ClaimPathFunction = external_publication_attempt_claim_path,
    phase283_function: ReconcileFunction = reconcile_external_publication_execution,
    phase306_function: Phase306Function = run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff,
) -> ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome:
    """Run Phase 306 at most once and persist one current-lineage outcome.

    The caller supplies only the exact Phase 303 authorization path and exact
    resume request.  All cycle-specific targets are derived from the exact
    loaded Phase 303 authorization digest.  An existing outcome is a fully
    revalidated, zero-Phase306 fast path.  Without an outcome, Phase 306 is
    called once, the durable start marker is read-only validated, and any
    existing canonical reconciliation evidence is observed read-only before
    one append-only outcome is persisted.
    """
    _preflight(
        start_authorization_path=start_authorization_path,
        request=request,
        authorization_loader=authorization_loader,
        authorization_digest_function=authorization_digest_function,
        approval_digest_function=approval_digest_function,
        start_loader=start_loader,
        start_digest_function=start_digest_function,
        reconciliation_loader=reconciliation_loader,
        reconciliation_digest_function=reconciliation_digest_function,
        phase280_consumption_key_function=phase280_consumption_key_function,
        phase280_claim_path_function=phase280_claim_path_function,
        phase283_function=phase283_function,
        phase306_function=phase306_function,
    )

    authorization = _load_authorization(authorization_loader, start_authorization_path)
    authorization_digest = _authorization_digest_once(
        authorization_digest_function, authorization
    )
    _validate_authorization_path(start_authorization_path, authorization)

    approval_digest = _approval_digest_once(approval_digest_function, request.approval)
    if (
        approval_digest != authorization.publication_approval_sha256
        or request.approval.publication_plan_sha256
        != authorization.publication_plan_sha256
    ):
        _raise_outcome("request_lineage")

    start_path = _derive_start_path(start_authorization_path, authorization_digest)
    reconciliation_path = _derive_reconciliation_path(
        start_authorization_path, authorization_digest
    )
    outcome_path = _derive_outcome_path(start_authorization_path, authorization_digest)
    if request.execution_reconciliation_evidence_path != reconciliation_path:
        _raise_outcome("reconciliation_path")
    _validate_outcome_target(outcome_path)

    if _target_present(outcome_path):
        return _resolve_existing_outcome(
            outcome_path=outcome_path,
            start_path=start_path,
            reconciliation_path=reconciliation_path,
            authorization=authorization,
            authorization_digest=authorization_digest,
            request=request,
            start_loader=start_loader,
            start_digest_function=start_digest_function,
            reconciliation_loader=reconciliation_loader,
            reconciliation_digest_function=reconciliation_digest_function,
            phase280_consumption_key_function=phase280_consumption_key_function,
            phase280_claim_path_function=phase280_claim_path_function,
            phase283_function=phase283_function,
        )

    result = _call_phase306(
        phase306_function,
        start_authorization_path=start_authorization_path,
        request=request,
    )
    start, start_digest = _load_and_bind_start(
        start_loader=start_loader,
        start_digest_function=start_digest_function,
        start_path=start_path,
        authorization=authorization,
    )

    if type(result) is ExternalPublicationExecutionReconciliation:
        _reconstruct_model(
            ExternalPublicationExecutionReconciliation,
            result,
            "reconciliation_contract",
        )
        state, result_kind, result_sha256 = _resolve_reconciliation_state(
            reconciliation_path=reconciliation_path,
            expected_result=result,
            request=request,
            reconciliation_loader=reconciliation_loader,
            reconciliation_digest_function=reconciliation_digest_function,
            phase280_consumption_key_function=phase280_consumption_key_function,
            phase280_claim_path_function=phase280_claim_path_function,
            phase283_function=phase283_function,
        )
    elif (
        type(result)
        is ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute
    ):
        _validate_recovery_route(result, authorization)
        if result.acquisition.start != start:
            _raise_outcome("start_lineage")
        state, result_kind, result_sha256 = _resolve_recovery_evidence(
            reconciliation_path=reconciliation_path,
            request=request,
            reconciliation_loader=reconciliation_loader,
            reconciliation_digest_function=reconciliation_digest_function,
            phase280_consumption_key_function=phase280_consumption_key_function,
            phase280_claim_path_function=phase280_claim_path_function,
            phase283_function=phase283_function,
        )
    else:
        _raise_outcome("phase306_contract")

    outcome = _construct_outcome(
        authorization=authorization,
        authorization_digest=authorization_digest,
        start_digest=start_digest,
        state=state,
        result_kind=result_kind,
        result_sha256=result_sha256,
    )
    _persist_outcome_once(outcome_path, outcome)

    if result_kind == "none" and _target_present(reconciliation_path):
        _raise_conflict()
    return outcome


def _preflight(
    *,
    start_authorization_path: object,
    request: object,
    authorization_loader: object,
    authorization_digest_function: object,
    approval_digest_function: object,
    start_loader: object,
    start_digest_function: object,
    reconciliation_loader: object,
    reconciliation_digest_function: object,
    phase280_consumption_key_function: object,
    phase280_claim_path_function: object,
    phase283_function: object,
    phase306_function: object,
) -> None:
    """Reject public contracts before any loader, filesystem, or Phase306 call."""
    if type(start_authorization_path) is not _PATH_TYPE:
        _raise_outcome("path_type")
    if type(request) is not ExternalPublicationResumeOperationRequest:
        _raise_outcome("request_contract")
    _require_exact_instance_fields(request, "request_contract")
    try:
        request_paths = (
            request.ledger_directory,
            request.execution_evidence_path,
            request.execution_reconciliation_evidence_path,
        )
        approval = request.approval
    except Exception:
        _raise_outcome("request_contract")
    if any(type(path) is not _PATH_TYPE for path in request_paths):
        _raise_outcome("path_type")
    if type(approval) is not ExternalPublicationApproval:
        _raise_outcome("request_contract")
    _reconstruct_approval(approval)

    dependencies = (
        authorization_loader,
        authorization_digest_function,
        approval_digest_function,
        start_loader,
        start_digest_function,
        reconciliation_loader,
        reconciliation_digest_function,
        phase280_consumption_key_function,
        phase280_claim_path_function,
        phase283_function,
        phase306_function,
    )
    if not all(callable(dependency) for dependency in dependencies):
        _raise_outcome("configuration")


def _load_authorization(
    loader: AuthorizationLoader, path: Path
) -> ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization:
    try:
        authorization = loader(path)
    except _KNOWN_AUTHORIZATION_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    _reconstruct_model(
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
        authorization,
        "authorization_contract",
    )
    return authorization  # type: ignore[return-value]


def _authorization_digest_once(
    digest_function: AuthorizationDigestFunction, authorization: object
) -> str:
    try:
        digest = digest_function(authorization)  # type: ignore[arg-type]
    except _KNOWN_AUTHORIZATION_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    if not _is_sha256(digest):
        _raise_outcome("authorization_digest")
    return digest


def _approval_digest_once(
    digest_function: ApprovalDigestFunction, approval: ExternalPublicationApproval
) -> str:
    try:
        digest = digest_function(approval)
    except ExternalPublicationError:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    if not _is_sha256(digest):
        _raise_outcome("request_lineage")
    return digest


def _start_digest_once(
    digest_function: StartDigestFunction, start: ExternalPublicationOperationStart
) -> str:
    try:
        digest = digest_function(start)
    except _KNOWN_START_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    if not _is_sha256(digest):
        _raise_outcome("start_digest")
    return digest


def _reconciliation_digest_once(
    digest_function: ReconciliationDigestFunction,
    reconciliation: ExternalPublicationExecutionReconciliation,
) -> str:
    try:
        digest = digest_function(reconciliation)
    except _KNOWN_RECONCILIATION_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    if not _is_sha256(digest):
        _raise_outcome("reconciliation_digest")
    return digest


def _call_phase306(
    phase306_function: Phase306Function,
    *,
    start_authorization_path: Path,
    request: ExternalPublicationResumeOperationRequest,
) -> object:
    try:
        return phase306_function(
            start_authorization_path=start_authorization_path,
            request=request,
        )
    except _KNOWN_PHASE306_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")


def _validate_authorization_path(
    path: Path,
    authorization: ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
) -> None:
    expected = path.parent / (
        f"{_AUTHORIZATION_FILENAME_PREFIX}"
        f"{authorization.decision_preparation_intent_binding_sha256}"
        f"{_FILENAME_SUFFIX}"
    )
    if path != expected:
        _raise_outcome("authorization_path")


def _derive_start_path(path: Path, authorization_digest: str) -> Path:
    return path.parent / (
        f"{_START_FILENAME_PREFIX}{authorization_digest}{_FILENAME_SUFFIX}"
    )


def _derive_reconciliation_path(path: Path, authorization_digest: str) -> Path:
    return path.parent / (
        f"{_RECONCILIATION_FILENAME_PREFIX}{authorization_digest}{_FILENAME_SUFFIX}"
    )


def _derive_outcome_path(path: Path, authorization_digest: str) -> Path:
    return path.parent / (
        f"{_OUTCOME_FILENAME_PREFIX}{authorization_digest}{_FILENAME_SUFFIX}"
    )


def _load_and_bind_start(
    *,
    start_loader: StartLoader,
    start_digest_function: StartDigestFunction,
    start_path: Path,
    authorization: ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
) -> tuple[ExternalPublicationOperationStart, str]:
    try:
        start = start_loader(start_path)
    except _KNOWN_START_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    _reconstruct_model(ExternalPublicationOperationStart, start, "start_contract")
    start_digest = _start_digest_once(start_digest_function, start)
    if start_digest != authorization.expected_operation_start_sha256:
        _raise_outcome("start_digest")
    if (
        start.operation_intent_sha256 != authorization.operation_intent_sha256
        or start.publication_approval_sha256
        != authorization.publication_approval_sha256
        or start.publication_plan_sha256 != authorization.publication_plan_sha256
        or start.operation != "resume"
        or start.state != "started"
    ):
        _raise_outcome("start_lineage")
    return start, start_digest  # type: ignore[return-value]


def _validate_recovery_route(
    route: ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute,
    authorization: ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
) -> None:
    try:
        _reconstruct_model(
            ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute,
            route,
            "phase306_contract",
        )
        acquisition = route.acquisition
        _reconstruct_model(
            ExternalPublicationOperationStartAcquisition,
            acquisition,
            "phase306_contract",
        )
        _reconstruct_model(
            ExternalPublicationOperationStart,
            acquisition.start,
            "phase306_contract",
        )
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        raise
    except Exception:
        _raise_outcome("phase306_contract")

    if (
        route.schema_version
        != "external-publication-recovery-resume-decision-preparation-start-acquisition-route.v1"
        or route.route != "recovery_required"
        or route.acquisition.status != "already_acquired"
    ):
        _raise_outcome("phase306_contract")
    start = route.acquisition.start
    if (
        start.operation_intent_sha256 != authorization.operation_intent_sha256
        or start.publication_approval_sha256
        != authorization.publication_approval_sha256
        or start.publication_plan_sha256 != authorization.publication_plan_sha256
        or start.operation != "resume"
        or start.state != "started"
    ):
        _raise_outcome("start_lineage")


def _resolve_recovery_evidence(
    *,
    reconciliation_path: Path,
    request: ExternalPublicationResumeOperationRequest,
    reconciliation_loader: ReconciliationLoader,
    reconciliation_digest_function: ReconciliationDigestFunction,
    phase280_consumption_key_function: ConsumptionKeyFunction,
    phase280_claim_path_function: ClaimPathFunction,
    phase283_function: ReconcileFunction,
) -> tuple[str, str, str | None]:
    if not _target_present(reconciliation_path):
        return "recovery_required", "none", None
    evidence = _load_reconciliation(reconciliation_loader, reconciliation_path)
    return _classify_durable_reconciliation(
        evidence=evidence,
        request=request,
        reconciliation_digest_function=reconciliation_digest_function,
        phase280_consumption_key_function=phase280_consumption_key_function,
        phase280_claim_path_function=phase280_claim_path_function,
        phase283_function=phase283_function,
    )


def _resolve_reconciliation_state(
    *,
    reconciliation_path: Path,
    expected_result: ExternalPublicationExecutionReconciliation,
    request: ExternalPublicationResumeOperationRequest,
    reconciliation_loader: ReconciliationLoader,
    reconciliation_digest_function: ReconciliationDigestFunction,
    phase280_consumption_key_function: ConsumptionKeyFunction,
    phase280_claim_path_function: ClaimPathFunction,
    phase283_function: ReconcileFunction,
) -> tuple[str, str, str | None]:
    if not _target_present(reconciliation_path):
        _raise_outcome("reconciliation_presence")
    evidence = _load_reconciliation(reconciliation_loader, reconciliation_path)
    if not _same_reconciliation(evidence, expected_result):
        _raise_outcome("reconciliation_lineage")
    return _classify_durable_reconciliation(
        evidence=evidence,
        request=request,
        reconciliation_digest_function=reconciliation_digest_function,
        phase280_consumption_key_function=phase280_consumption_key_function,
        phase280_claim_path_function=phase280_claim_path_function,
        phase283_function=phase283_function,
    )


def _load_reconciliation(
    loader: ReconciliationLoader, path: Path
) -> ExternalPublicationExecutionReconciliation:
    try:
        reconciliation = loader(path)
    except _KNOWN_RECONCILIATION_ERRORS:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    _reconstruct_model(
        ExternalPublicationExecutionReconciliation,
        reconciliation,
        "reconciliation_contract",
    )
    return reconciliation  # type: ignore[return-value]


def _classify_durable_reconciliation(
    *,
    evidence: ExternalPublicationExecutionReconciliation,
    request: ExternalPublicationResumeOperationRequest,
    reconciliation_digest_function: ReconciliationDigestFunction,
    phase280_consumption_key_function: ConsumptionKeyFunction,
    phase280_claim_path_function: ClaimPathFunction,
    phase283_function: ReconcileFunction,
) -> tuple[str, str, str | None]:
    evidence_digest = _reconciliation_digest_once(
        reconciliation_digest_function, evidence
    )
    observed = _observe_reconciliation(
        request=request,
        phase280_consumption_key_function=phase280_consumption_key_function,
        phase280_claim_path_function=phase280_claim_path_function,
        phase283_function=phase283_function,
    )
    if not _same_reconciliation(observed, evidence):
        _raise_outcome("reconciliation_lineage")
    if evidence.status == "matched":
        return "completed", "reconciliation", evidence_digest
    if evidence.status == "lineage_mismatch":
        return "recovery_required", "reconciliation", evidence_digest
    _raise_outcome("reconciliation_contract")


def _observe_reconciliation(
    *,
    request: ExternalPublicationResumeOperationRequest,
    phase280_consumption_key_function: ConsumptionKeyFunction,
    phase280_claim_path_function: ClaimPathFunction,
    phase283_function: ReconcileFunction,
) -> ExternalPublicationExecutionReconciliation:
    try:
        consumption_key = phase280_consumption_key_function(request.approval)
    except ExternalPublicationError:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    if not _is_sha256(consumption_key):
        _raise_outcome("reconciliation_contract")

    try:
        claim_path = phase280_claim_path_function(
            request.ledger_directory, consumption_key
        )
    except ExternalPublicationError:
        raise
    except Exception:
        _raise_outcome("dependency_error")
    if type(claim_path) is not _PATH_TYPE:
        _raise_outcome("reconciliation_contract")

    try:
        observed = phase283_function(
            claim_path=claim_path,
            execution_evidence_path=request.execution_evidence_path,
        )
    except _KNOWN_RECONCILIATION_ERRORS + (ExternalPublicationError,):
        raise
    except Exception:
        _raise_outcome("dependency_error")
    _reconstruct_model(
        ExternalPublicationExecutionReconciliation,
        observed,
        "reconciliation_contract",
    )
    return observed  # type: ignore[return-value]


def _construct_outcome(
    *,
    authorization: ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
    authorization_digest: str,
    start_digest: str,
    state: str,
    result_kind: str,
    result_sha256: str | None,
) -> ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome:
    try:
        return (
            ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome(
                schema_version=_OUTCOME_SCHEMA_VERSION,
                decision_preparation_start_authorization_sha256=authorization_digest,
                decision_preparation_intent_binding_sha256=(
                    authorization.decision_preparation_intent_binding_sha256
                ),
                decision_preparation_sha256=authorization.decision_preparation_sha256,
                recovery_resume_decision_sha256=(
                    authorization.recovery_resume_decision_sha256
                ),
                operation_intent_sha256=authorization.operation_intent_sha256,
                operation_start_sha256=start_digest,
                publication_approval_sha256=authorization.publication_approval_sha256,
                publication_plan_sha256=authorization.publication_plan_sha256,
                source_operation=authorization.source_operation,
                previous_recovery_kind=authorization.previous_recovery_kind,
                recovery_kind=authorization.recovery_kind,
                operation="resume",
                state=state,  # type: ignore[arg-type]
                result_kind=result_kind,  # type: ignore[arg-type]
                result_sha256=result_sha256,
            )
        )
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        raise
    except Exception:
        _raise_outcome("outcome_contract")


def _resolve_existing_outcome(
    *,
    outcome_path: Path,
    start_path: Path,
    reconciliation_path: Path,
    authorization: ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
    authorization_digest: str,
    request: ExternalPublicationResumeOperationRequest,
    start_loader: StartLoader,
    start_digest_function: StartDigestFunction,
    reconciliation_loader: ReconciliationLoader,
    reconciliation_digest_function: ReconciliationDigestFunction,
    phase280_consumption_key_function: ConsumptionKeyFunction,
    phase280_claim_path_function: ClaimPathFunction,
    phase283_function: ReconcileFunction,
) -> ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome:
    outcome = _load_existing_outcome(outcome_path)
    _validate_outcome_lineage(outcome, authorization, authorization_digest)
    _durable_start, start_digest = _load_and_bind_start(
        start_loader=start_loader,
        start_digest_function=start_digest_function,
        start_path=start_path,
        authorization=authorization,
    )
    if outcome.operation_start_sha256 != start_digest:
        _raise_outcome("outcome_lineage")

    if outcome.result_kind == "none":
        if _target_present(reconciliation_path):
            _raise_conflict()
        return outcome
    if outcome.result_kind != "reconciliation":
        _raise_outcome("outcome_contract")
    if not _target_present(reconciliation_path):
        _raise_outcome("reconciliation_presence")
    evidence = _load_reconciliation(reconciliation_loader, reconciliation_path)
    evidence_digest = _reconciliation_digest_once(
        reconciliation_digest_function, evidence
    )
    if evidence_digest != outcome.result_sha256:
        _raise_outcome("outcome_lineage")
    observed = _observe_reconciliation(
        request=request,
        phase280_consumption_key_function=phase280_consumption_key_function,
        phase280_claim_path_function=phase280_claim_path_function,
        phase283_function=phase283_function,
    )
    if not _same_reconciliation(observed, evidence):
        _raise_outcome("reconciliation_lineage")
    expected_state = (
        "completed" if evidence.status == "matched" else "recovery_required"
    )
    if outcome.state != expected_state:
        _raise_outcome("outcome_lineage")
    return outcome


def _load_existing_outcome(
    path: Path,
) -> ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome:
    try:
        outcome = load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            path
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeLoadError:
        raise
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        raise
    except Exception:
        _raise_outcome("dependency_error")
    _reconstruct_model(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome,
        outcome,
        "outcome_contract",
    )
    return outcome  # type: ignore[return-value]


def _validate_outcome_lineage(
    outcome: ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome,
    authorization: ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
    authorization_digest: str,
) -> None:
    if (
        outcome.decision_preparation_start_authorization_sha256 != authorization_digest
        or outcome.decision_preparation_intent_binding_sha256
        != authorization.decision_preparation_intent_binding_sha256
        or outcome.decision_preparation_sha256
        != authorization.decision_preparation_sha256
        or outcome.recovery_resume_decision_sha256
        != authorization.recovery_resume_decision_sha256
        or outcome.operation_intent_sha256 != authorization.operation_intent_sha256
        or outcome.publication_approval_sha256
        != authorization.publication_approval_sha256
        or outcome.publication_plan_sha256 != authorization.publication_plan_sha256
        or outcome.source_operation != authorization.source_operation
        or outcome.previous_recovery_kind != authorization.previous_recovery_kind
        or outcome.recovery_kind != authorization.recovery_kind
        or outcome.operation != "resume"
    ):
        _raise_outcome("outcome_lineage")


def _same_reconciliation(left: object, right: object) -> bool:
    if (
        type(left) is not ExternalPublicationExecutionReconciliation
        or type(right) is not ExternalPublicationExecutionReconciliation
    ):
        return False
    return all(
        getattr(left, field.name) == getattr(right, field.name)
        for field in fields(ExternalPublicationExecutionReconciliation)
    )


def _persist_outcome_once(
    path: Path,
    outcome: ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome,
) -> None:
    try:
        result = persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
            path, outcome
        )
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        raise
    except Exception:
        _raise_outcome("dependency_error")
    if result is not None:
        _raise_outcome("dependency_error")


def _reconstruct_approval(approval: object) -> None:
    try:
        _require_exact_instance_fields(approval, "request_contract")
        values = {
            field.name: getattr(approval, field.name)
            for field in fields(ExternalPublicationApproval)
        }
        ExternalPublicationApproval(**values)
    except ExternalPublicationError:
        raise
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        raise
    except Exception:
        _raise_outcome("request_contract")


def _reconstruct_model(
    model_type: type[object], instance: object, classification: Classification
) -> None:
    if type(instance) is not model_type:
        _raise_outcome(classification)
    try:
        _require_exact_instance_fields(instance, classification)
        values = {
            field.name: getattr(instance, field.name) for field in fields(model_type)
        }
        model_type(**values)  # type: ignore[operator]
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        raise
    except Exception:
        _raise_outcome(classification)


def _require_exact_instance_fields(
    instance: object, classification: Classification
) -> None:
    try:
        expected = {field.name for field in fields(type(instance))}
        if set(vars(instance)) != expected:
            _raise_outcome(classification)
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        raise
    except Exception:
        _raise_outcome(classification)


def _validate_outcome(outcome: object) -> None:
    if (
        type(outcome)
        is not ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome
    ):
        _raise_outcome("configuration")
    try:
        if set(vars(outcome)) != {
            field.name
            for field in fields(
                ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome
            )
        }:
            _raise_outcome("configuration")
        values = {
            field.name: getattr(outcome, field.name)
            for field in fields(
                ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome
            )
        }
        if (
            type(values["schema_version"]) is not str
            or values["schema_version"] != _OUTCOME_SCHEMA_VERSION
        ):
            _raise_outcome("configuration")
        for field_name in (
            "decision_preparation_start_authorization_sha256",
            "decision_preparation_intent_binding_sha256",
            "decision_preparation_sha256",
            "recovery_resume_decision_sha256",
            "operation_intent_sha256",
            "operation_start_sha256",
            "publication_approval_sha256",
            "publication_plan_sha256",
        ):
            if not _is_sha256(values[field_name]):
                _raise_outcome("configuration")
        if (
            type(values["source_operation"]) is not str
            or values["source_operation"] not in _SOURCE_OPERATIONS
        ):
            _raise_outcome("configuration")
        for field_name in ("previous_recovery_kind", "recovery_kind"):
            if (
                type(values[field_name]) is not str
                or values[field_name] not in _RECOVERY_KINDS
            ):
                _raise_outcome("configuration")
        if (
            values["previous_recovery_kind"] == "reconciliation_mismatch"
            and values["source_operation"] != "resume"
        ):
            _raise_outcome("configuration")
        if type(values["operation"]) is not str or values["operation"] != "resume":
            _raise_outcome("configuration")
        if type(values["state"]) is not str or values["state"] not in _OUTCOME_STATES:
            _raise_outcome("configuration")
        if (
            type(values["result_kind"]) is not str
            or values["result_kind"] not in _RESULT_KINDS
        ):
            _raise_outcome("configuration")
        result_kind = values["result_kind"]
        result_sha256 = values["result_sha256"]
        if result_kind == "none":
            if result_sha256 is not None or values["state"] != "recovery_required":
                _raise_outcome("configuration")
        else:
            if not _is_sha256(result_sha256):
                _raise_outcome("configuration")
        if values["state"] == "completed" and result_kind != "reconciliation":
            _raise_outcome("configuration")
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        raise
    except Exception:
        _raise_outcome("configuration")


def _validate_outcome_target(path: Path) -> None:
    try:
        if not path.parent.exists() or not path.parent.is_dir():
            _raise_outcome("parent")
        if path.is_symlink() or path.is_dir() or (path.exists() and not path.is_file()):
            _raise_outcome("target")
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        raise
    except Exception:
        _raise_outcome("target")


def _target_present(path: Path) -> bool:
    try:
        return path.is_symlink() or path.exists()
    except Exception:
        _raise_outcome("load")


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
    except ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomePersistenceError:
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
    except ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeLoadError:
        raise
    except Exception:
        _raise_load("target")


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
                raise OSError("short Phase 307 outcome write")
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
    except ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomePersistenceError:
        raise
    except Exception:
        _raise_persistence("target")
    if existing != contents:
        _raise_conflict()
    try:
        handle = path.open("rb")
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
            raise OSError("Phase 307 outcome handle cannot close")
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


def _parse_outcome(
    value: object,
) -> ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome:
    if type(value) is not dict:
        _raise_load("parse")
    if frozenset(value) != _OUTCOME_KEYS:
        _raise_load("keys")
    try:
        return (
            ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome(
                schema_version=value["schema_version"],
                decision_preparation_start_authorization_sha256=value[
                    "decision_preparation_start_authorization_sha256"
                ],
                decision_preparation_intent_binding_sha256=value[
                    "decision_preparation_intent_binding_sha256"
                ],
                decision_preparation_sha256=value["decision_preparation_sha256"],
                recovery_resume_decision_sha256=value[
                    "recovery_resume_decision_sha256"
                ],
                operation_intent_sha256=value["operation_intent_sha256"],
                operation_start_sha256=value["operation_start_sha256"],
                publication_approval_sha256=value["publication_approval_sha256"],
                publication_plan_sha256=value["publication_plan_sha256"],
                source_operation=value["source_operation"],
                previous_recovery_kind=value["previous_recovery_kind"],
                recovery_kind=value["recovery_kind"],
                operation=value["operation"],
                state=value["state"],
                result_kind=value["result_kind"],
                result_sha256=value["result_sha256"],
            )
        )
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError
    ):
        _raise_load("load")
    except Exception:
        _raise_load("parse")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


class _DuplicateKeyError(ValueError):
    """Raised when a canonical Phase 307 artifact repeats a key."""


class _NonStandardJSONConstantError(ValueError):
    """Raised when a canonical Phase 307 artifact uses NaN or infinity."""


def _reject_nonstandard_json_constant(value: str) -> NoReturn:
    raise _NonStandardJSONConstantError(value)


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _raise_outcome(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeCompatibilityError(
        classification
    ) from None


def _raise_persistence(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomePersistenceError(
        classification
    ) from None


def _raise_conflict() -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeConflictError(
        "conflict"
    ) from None


def _raise_load(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeLoadError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome",
    "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeCompatibilityError",
    "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeConflictError",
    "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError",
    "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeFailureDetail",
    "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeLoadError",
    "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomePersistenceError",
    "external_publication_recovery_resume_decision_preparation_reconciliation_outcome_canonical_bytes",
    "external_publication_recovery_resume_decision_preparation_reconciliation_outcome_digest",
    "load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome",
    "persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome",
    "run_and_persist_external_publication_recovery_resume_decision_preparation_reconciliation_outcome",
    "serialize_external_publication_recovery_resume_decision_preparation_reconciliation_outcome_canonical",
]
