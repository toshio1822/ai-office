# ruff: noqa: E501

"""Read-only routing for the durable Phase 307 reconciliation outcome.

Phase 308 consumes only the exact durable Phase 302 intent-binding path.  It
strict-loads and revalidates the Phase 302 binding, Phase 303 authorization,
Phase 307 outcome, Phase 290 start marker, and authorization-specific
reconciliation evidence, then routes the exact Phase 307 outcome without
calling any execution, orchestration, observation, or persistence boundary.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal, NoReturn

from .external_publication_execution_reconciliation import (
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationExecutionReconciliationError,
)
from .external_publication_execution_reconciliation_evidence import (
    ExternalPublicationExecutionReconciliationEvidenceError,
    external_publication_execution_reconciliation_digest,
    load_external_publication_execution_reconciliation,
)
from .external_publication_operation_start import (
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartError,
    external_publication_operation_start_digest,
    load_external_publication_operation_start,
)
from .external_publication_recovery_resume_decision_preparation_intent_binding import (
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding,
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError,
    external_publication_recovery_resume_decision_preparation_intent_binding_digest,
    load_external_publication_recovery_resume_decision_preparation_intent_binding,
)
from .external_publication_recovery_resume_decision_preparation_reconciliation_outcome import (
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome,
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError,
    external_publication_recovery_resume_decision_preparation_reconciliation_outcome_digest,
    load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome,
)
from .external_publication_recovery_resume_decision_preparation_start_authorization import (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError,
    external_publication_recovery_resume_decision_preparation_start_authorization_digest,
    load_external_publication_recovery_resume_decision_preparation_start_authorization,
)

_ROUTING_ERROR_MESSAGE = (
    "external publication recovery resume decision preparation reconciliation "
    "outcome routing is blocked"
)
_DECISION_SCHEMA_VERSION = (
    "external-publication-recovery-resume-decision-preparation-reconciliation-"
    "decision-required.v1"
)
_BINDING_FILENAME_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-intent-binding-"
)
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
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_SOURCE_OPERATIONS = frozenset({"fresh", "resume"})
_RECOVERY_KINDS = frozenset({"already_acquired", "reconciliation_mismatch"})
_RESULT_KINDS = frozenset({"reconciliation", "none"})
_COMPLETED_STATE = "completed"
_RECOVERY_REQUIRED_STATE = "recovery_required"
_RECONCILIATION_RESULT_KIND = "reconciliation"
_NONE_RESULT_KIND = "none"
_OPERATION = "resume"
_AUTHORIZATION_STATE = "authorized"
_START_STATE = "started"
_DECISION_STATE = "decision_required"
_MATCHED_STATUS = "matched"
_LINEAGE_MISMATCH_STATUS = "lineage_mismatch"
_MISMATCH_RECOVERY_KIND = "reconciliation_mismatch"
_ALREADY_ACQUIRED_RECOVERY_KIND = "already_acquired"

Classification = Literal[
    "configuration",
    "path_type",
    "binding_contract",
    "binding_digest",
    "binding_path",
    "authorization_contract",
    "authorization_digest",
    "authorization_lineage",
    "outcome_contract",
    "outcome_digest",
    "outcome_lineage",
    "start_contract",
    "start_digest",
    "start_lineage",
    "reconciliation_contract",
    "reconciliation_digest",
    "reconciliation_lineage",
    "reconciliation_presence",
    "route_contract",
    "dependency_error",
]

BindingLoader = Callable[[Path], object]
BindingDigestFunction = Callable[
    [ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding], object
]
AuthorizationLoader = Callable[[Path], object]
AuthorizationDigestFunction = Callable[
    [ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization], object
]
OutcomeLoader = Callable[[Path], object]
OutcomeDigestFunction = Callable[
    [ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome],
    object,
]
StartLoader = Callable[[Path], object]
StartDigestFunction = Callable[[ExternalPublicationOperationStart], object]
ReconciliationLoader = Callable[[Path], object]
ReconciliationDigestFunction = Callable[
    [ExternalPublicationExecutionReconciliation], object
]

_KNOWN_BINDING_ERRORS = (
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError,
)
_KNOWN_AUTHORIZATION_ERRORS = (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError,
)
_KNOWN_OUTCOME_ERRORS = (
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeError,
)
_KNOWN_START_ERRORS = (ExternalPublicationOperationStartError,)
_KNOWN_RECONCILIATION_ERRORS = (
    ExternalPublicationExecutionReconciliationError,
    ExternalPublicationExecutionReconciliationEvidenceError,
)


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingFailureDetail:
    """Detail-safe classification for one Phase 308 failure."""

    classification: Classification


class ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingError(
    ValueError
):
    """Raised when a Phase 308 input, artifact, or route is not exact."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_ROUTING_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingCompatibilityError(
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingError
):
    """Raised when a durable lineage or routing contract is incompatible."""


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeDecisionPreparationReconciliationDecisionRequired:
    """Immutable in-memory decision-required route for one Phase 307 outcome.

    ``previous_recovery_kind`` preserves the historical reason recorded by
    Phase 307.  ``recovery_kind`` is the current reason derived from the
    current Phase 307 state/result pair.  This model is intentionally not
    serialized, digested, loaded, or persisted by Phase 308.
    """

    schema_version: Literal[
        "external-publication-recovery-resume-decision-preparation-reconciliation-decision-required.v1"
    ]
    decision_preparation_reconciliation_outcome_sha256: str
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
    result_kind: Literal["reconciliation", "none"]
    result_sha256: str | None
    state: Literal["decision_required"]

    def __post_init__(self) -> None:
        _validate_decision(self)


def route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome(
    *,
    decision_preparation_intent_binding_path: Path,
    binding_loader: BindingLoader = (
        load_external_publication_recovery_resume_decision_preparation_intent_binding
    ),
    binding_digest_function: BindingDigestFunction = (
        external_publication_recovery_resume_decision_preparation_intent_binding_digest
    ),
    authorization_loader: AuthorizationLoader = (
        load_external_publication_recovery_resume_decision_preparation_start_authorization
    ),
    authorization_digest_function: AuthorizationDigestFunction = (
        external_publication_recovery_resume_decision_preparation_start_authorization_digest
    ),
    outcome_loader: OutcomeLoader = (
        load_external_publication_recovery_resume_decision_preparation_reconciliation_outcome
    ),
    outcome_digest_function: OutcomeDigestFunction = (
        external_publication_recovery_resume_decision_preparation_reconciliation_outcome_digest
    ),
    start_loader: StartLoader = load_external_publication_operation_start,
    start_digest_function: StartDigestFunction = (
        external_publication_operation_start_digest
    ),
    reconciliation_loader: ReconciliationLoader = (
        load_external_publication_execution_reconciliation
    ),
    reconciliation_digest_function: ReconciliationDigestFunction = (
        external_publication_execution_reconciliation_digest
    ),
) -> (
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome
    | ExternalPublicationRecoveryResumeDecisionPreparationReconciliationDecisionRequired
):
    """Route one exact durable Phase 307 outcome without mutating storage.

    The caller supplies only the exact concrete Phase 302 binding path.  All
    later paths are derived from the exact loaded binding and authorization
    digests.  Every predecessor model is independently reconstructed before
    its lineage is consumed.  Completed reconciliation is returned by exact
    identity; recovery-required outcomes become one in-memory decision route.
    """
    _preflight(
        decision_preparation_intent_binding_path=decision_preparation_intent_binding_path,
        binding_loader=binding_loader,
        binding_digest_function=binding_digest_function,
        authorization_loader=authorization_loader,
        authorization_digest_function=authorization_digest_function,
        outcome_loader=outcome_loader,
        outcome_digest_function=outcome_digest_function,
        start_loader=start_loader,
        start_digest_function=start_digest_function,
        reconciliation_loader=reconciliation_loader,
        reconciliation_digest_function=reconciliation_digest_function,
    )

    binding = _strict_load_binding(
        binding_loader, decision_preparation_intent_binding_path
    )
    binding_digest = _binding_digest_once(binding_digest_function, binding)
    _validate_binding_path(decision_preparation_intent_binding_path, binding)

    authorization_path = _derive_authorization_path(
        decision_preparation_intent_binding_path, binding_digest
    )
    authorization = _strict_load_authorization(authorization_loader, authorization_path)
    authorization_digest = _authorization_digest_once(
        authorization_digest_function, authorization
    )
    _validate_authorization_lineage(
        binding=binding,
        authorization=authorization,
        binding_digest=binding_digest,
    )

    start_path = _derive_start_path(
        decision_preparation_intent_binding_path, authorization_digest
    )
    reconciliation_path = _derive_reconciliation_path(
        decision_preparation_intent_binding_path, authorization_digest
    )
    outcome_path = _derive_outcome_path(
        decision_preparation_intent_binding_path, authorization_digest
    )

    outcome = _strict_load_outcome(outcome_loader, outcome_path)
    _validate_outcome_lineage(
        binding=binding,
        authorization=authorization,
        outcome=outcome,
        binding_digest=binding_digest,
        authorization_digest=authorization_digest,
    )

    start = _strict_load_start(start_loader, start_path)
    start_digest = _start_digest_once(start_digest_function, start)
    _validate_start_lineage(
        authorization=authorization,
        outcome=outcome,
        start=start,
        start_digest=start_digest,
    )

    _validate_durable_result(
        outcome=outcome,
        reconciliation_path=reconciliation_path,
        reconciliation_loader=reconciliation_loader,
        reconciliation_digest_function=reconciliation_digest_function,
    )

    if outcome.state == _COMPLETED_STATE:  # type: ignore[union-attr]
        return outcome  # type: ignore[return-value]
    if outcome.state != _RECOVERY_REQUIRED_STATE:  # type: ignore[union-attr]
        _raise_routing("route_contract")

    outcome_digest = _outcome_digest_once(outcome_digest_function, outcome)
    try:
        return ExternalPublicationRecoveryResumeDecisionPreparationReconciliationDecisionRequired(
            schema_version=_DECISION_SCHEMA_VERSION,
            decision_preparation_reconciliation_outcome_sha256=outcome_digest,
            decision_preparation_start_authorization_sha256=authorization_digest,
            decision_preparation_intent_binding_sha256=binding_digest,
            decision_preparation_sha256=outcome.decision_preparation_sha256,  # type: ignore[union-attr]
            recovery_resume_decision_sha256=outcome.recovery_resume_decision_sha256,  # type: ignore[union-attr]
            operation_intent_sha256=outcome.operation_intent_sha256,  # type: ignore[union-attr]
            operation_start_sha256=start_digest,
            publication_approval_sha256=outcome.publication_approval_sha256,  # type: ignore[union-attr]
            publication_plan_sha256=outcome.publication_plan_sha256,  # type: ignore[union-attr]
            source_operation=outcome.source_operation,  # type: ignore[union-attr]
            previous_recovery_kind=outcome.recovery_kind,  # type: ignore[union-attr]
            recovery_kind=(
                _MISMATCH_RECOVERY_KIND
                if outcome.result_kind == _RECONCILIATION_RESULT_KIND  # type: ignore[union-attr]
                else _ALREADY_ACQUIRED_RECOVERY_KIND
            ),
            operation=_OPERATION,
            result_kind=outcome.result_kind,  # type: ignore[union-attr]
            result_sha256=outcome.result_sha256,  # type: ignore[union-attr]
            state=_DECISION_STATE,
        )
    except ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingError:
        raise
    except Exception:
        _raise_routing("route_contract")


def _preflight(
    *,
    decision_preparation_intent_binding_path: object,
    binding_loader: object,
    binding_digest_function: object,
    authorization_loader: object,
    authorization_digest_function: object,
    outcome_loader: object,
    outcome_digest_function: object,
    start_loader: object,
    start_digest_function: object,
    reconciliation_loader: object,
    reconciliation_digest_function: object,
) -> None:
    """Reject public contracts before any loader or filesystem access."""
    if type(decision_preparation_intent_binding_path) is not _PATH_TYPE:
        _raise_routing("path_type")
    dependencies = (
        binding_loader,
        binding_digest_function,
        authorization_loader,
        authorization_digest_function,
        outcome_loader,
        outcome_digest_function,
        start_loader,
        start_digest_function,
        reconciliation_loader,
        reconciliation_digest_function,
    )
    if not all(callable(dependency) for dependency in dependencies):
        _raise_routing("configuration")


def _strict_load_binding(loader: BindingLoader, path: Path) -> object:
    try:
        binding = loader(path)
    except _KNOWN_BINDING_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if (
        type(binding)
        is not ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding
    ):
        _raise_routing("binding_contract")
    _reconstruct_model(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding,
        binding,
        "binding_contract",
    )
    return binding


def _binding_digest_once(
    digest_function: BindingDigestFunction, binding: object
) -> str:
    try:
        digest = digest_function(binding)  # type: ignore[arg-type]
    except _KNOWN_BINDING_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if not _is_sha256(digest):
        _raise_routing("binding_digest")
    return digest


def _validate_binding_path(
    path: Path,
    binding: ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding,
) -> None:
    expected = path.parent / (
        f"{_BINDING_FILENAME_PREFIX}"
        f"{binding.decision_preparation_sha256}"
        f"{_FILENAME_SUFFIX}"
    )
    if path != expected:
        _raise_routing("binding_path")


def _derive_authorization_path(path: Path, binding_digest: str) -> Path:
    return path.parent / (
        f"{_AUTHORIZATION_FILENAME_PREFIX}{binding_digest}{_FILENAME_SUFFIX}"
    )


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


def _strict_load_authorization(loader: AuthorizationLoader, path: Path) -> object:
    try:
        authorization = loader(path)
    except _KNOWN_AUTHORIZATION_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if (
        type(authorization)
        is not ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization
    ):
        _raise_routing("authorization_contract")
    _reconstruct_model(
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
        authorization,
        "authorization_contract",
    )
    return authorization


def _authorization_digest_once(
    digest_function: AuthorizationDigestFunction, authorization: object
) -> str:
    try:
        digest = digest_function(authorization)  # type: ignore[arg-type]
    except _KNOWN_AUTHORIZATION_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if not _is_sha256(digest):
        _raise_routing("authorization_digest")
    return digest


def _validate_authorization_lineage(
    *,
    binding: object,
    authorization: object,
    binding_digest: str,
) -> None:
    pairs = (
        (
            authorization.decision_preparation_intent_binding_sha256,  # type: ignore[union-attr]
            binding_digest,
        ),
        (
            authorization.decision_preparation_sha256,  # type: ignore[union-attr]
            binding.decision_preparation_sha256,  # type: ignore[union-attr]
        ),
        (
            authorization.recovery_resume_decision_sha256,  # type: ignore[union-attr]
            binding.recovery_resume_decision_sha256,  # type: ignore[union-attr]
        ),
        (
            authorization.operation_intent_sha256,  # type: ignore[union-attr]
            binding.operation_intent_sha256,  # type: ignore[union-attr]
        ),
        (
            authorization.publication_approval_sha256,  # type: ignore[union-attr]
            binding.publication_approval_sha256,  # type: ignore[union-attr]
        ),
        (
            authorization.publication_plan_sha256,  # type: ignore[union-attr]
            binding.publication_plan_sha256,  # type: ignore[union-attr]
        ),
        (
            authorization.source_operation,  # type: ignore[union-attr]
            binding.source_operation,  # type: ignore[union-attr]
        ),
        (
            authorization.previous_recovery_kind,  # type: ignore[union-attr]
            binding.previous_recovery_kind,  # type: ignore[union-attr]
        ),
        (
            authorization.recovery_kind,  # type: ignore[union-attr]
            binding.recovery_kind,  # type: ignore[union-attr]
        ),
        (
            authorization.result_kind,  # type: ignore[union-attr]
            binding.result_kind,  # type: ignore[union-attr]
        ),
        (
            authorization.result_sha256,  # type: ignore[union-attr]
            binding.result_sha256,  # type: ignore[union-attr]
        ),
        (
            authorization.operation,  # type: ignore[union-attr]
            binding.operation,  # type: ignore[union-attr]
        ),
    )
    if any(left != right for left, right in pairs):
        _raise_routing("authorization_lineage")
    if (
        binding.state != _AUTHORIZATION_STATE  # type: ignore[union-attr]
        or authorization.state != _AUTHORIZATION_STATE  # type: ignore[union-attr]
        or authorization.operation != _OPERATION  # type: ignore[union-attr]
    ):
        _raise_routing("authorization_lineage")


def _strict_load_outcome(loader: OutcomeLoader, path: Path) -> object:
    try:
        outcome = loader(path)
    except _KNOWN_OUTCOME_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if (
        type(outcome)
        is not ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome
    ):
        _raise_routing("outcome_contract")
    _reconstruct_model(
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcome,
        outcome,
        "outcome_contract",
    )
    return outcome


def _validate_outcome_lineage(
    *,
    binding: object,
    authorization: object,
    outcome: object,
    binding_digest: str,
    authorization_digest: str,
) -> None:
    pairs = (
        (
            outcome.decision_preparation_start_authorization_sha256,  # type: ignore[union-attr]
            authorization_digest,
        ),
        (
            outcome.decision_preparation_intent_binding_sha256,  # type: ignore[union-attr]
            binding_digest,
        ),
        (
            outcome.decision_preparation_sha256,  # type: ignore[union-attr]
            authorization.decision_preparation_sha256,  # type: ignore[union-attr]
        ),
        (
            outcome.decision_preparation_sha256,  # type: ignore[union-attr]
            binding.decision_preparation_sha256,  # type: ignore[union-attr]
        ),
        (
            outcome.recovery_resume_decision_sha256,  # type: ignore[union-attr]
            authorization.recovery_resume_decision_sha256,  # type: ignore[union-attr]
        ),
        (
            outcome.recovery_resume_decision_sha256,  # type: ignore[union-attr]
            binding.recovery_resume_decision_sha256,  # type: ignore[union-attr]
        ),
        (
            outcome.operation_intent_sha256,  # type: ignore[union-attr]
            authorization.operation_intent_sha256,  # type: ignore[union-attr]
        ),
        (
            outcome.operation_intent_sha256,  # type: ignore[union-attr]
            binding.operation_intent_sha256,  # type: ignore[union-attr]
        ),
        (
            outcome.operation_start_sha256,  # type: ignore[union-attr]
            authorization.expected_operation_start_sha256,  # type: ignore[union-attr]
        ),
        (
            outcome.publication_approval_sha256,  # type: ignore[union-attr]
            authorization.publication_approval_sha256,  # type: ignore[union-attr]
        ),
        (
            outcome.publication_plan_sha256,  # type: ignore[union-attr]
            authorization.publication_plan_sha256,  # type: ignore[union-attr]
        ),
        (
            outcome.source_operation,  # type: ignore[union-attr]
            authorization.source_operation,  # type: ignore[union-attr]
        ),
        (
            outcome.previous_recovery_kind,  # type: ignore[union-attr]
            authorization.previous_recovery_kind,  # type: ignore[union-attr]
        ),
        (
            outcome.recovery_kind,  # type: ignore[union-attr]
            authorization.recovery_kind,  # type: ignore[union-attr]
        ),
        (
            outcome.result_kind,  # type: ignore[union-attr]
            authorization.result_kind,  # type: ignore[union-attr]
        ),
        (
            outcome.result_sha256,  # type: ignore[union-attr]
            authorization.result_sha256,  # type: ignore[union-attr]
        ),
        (
            outcome.operation,  # type: ignore[union-attr]
            authorization.operation,  # type: ignore[union-attr]
        ),
    )
    if any(left != right for left, right in pairs):
        _raise_routing("outcome_lineage")
    if (
        outcome.operation != _OPERATION  # type: ignore[union-attr]
        or outcome.source_operation not in _SOURCE_OPERATIONS  # type: ignore[union-attr]
    ):
        _raise_routing("outcome_lineage")


def _strict_load_start(loader: StartLoader, path: Path) -> object:
    try:
        start = loader(path)
    except _KNOWN_START_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if type(start) is not ExternalPublicationOperationStart:
        _raise_routing("start_contract")
    _reconstruct_model(ExternalPublicationOperationStart, start, "start_contract")
    return start


def _start_digest_once(digest_function: StartDigestFunction, start: object) -> str:
    try:
        digest = digest_function(start)  # type: ignore[arg-type]
    except _KNOWN_START_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if not _is_sha256(digest):
        _raise_routing("start_digest")
    return digest


def _validate_start_lineage(
    *,
    authorization: object,
    outcome: object,
    start: object,
    start_digest: str,
) -> None:
    if (
        start_digest != authorization.expected_operation_start_sha256  # type: ignore[union-attr]
    ):
        _raise_routing("start_digest")
    if start_digest != outcome.operation_start_sha256:  # type: ignore[union-attr]
        _raise_routing("start_lineage")
    pairs = (
        (
            start.operation_intent_sha256,  # type: ignore[union-attr]
            authorization.operation_intent_sha256,  # type: ignore[union-attr]
        ),
        (
            start.operation_intent_sha256,  # type: ignore[union-attr]
            outcome.operation_intent_sha256,  # type: ignore[union-attr]
        ),
        (
            start.publication_approval_sha256,  # type: ignore[union-attr]
            authorization.publication_approval_sha256,  # type: ignore[union-attr]
        ),
        (
            start.publication_approval_sha256,  # type: ignore[union-attr]
            outcome.publication_approval_sha256,  # type: ignore[union-attr]
        ),
        (
            start.publication_plan_sha256,  # type: ignore[union-attr]
            authorization.publication_plan_sha256,  # type: ignore[union-attr]
        ),
        (
            start.publication_plan_sha256,  # type: ignore[union-attr]
            outcome.publication_plan_sha256,  # type: ignore[union-attr]
        ),
    )
    if any(left != right for left, right in pairs):
        _raise_routing("start_lineage")
    if start.operation != _OPERATION or start.state != _START_STATE:  # type: ignore[union-attr]
        _raise_routing("start_lineage")


def _target_present(path: Path) -> bool:
    try:
        return path.is_symlink() or path.exists()
    except Exception:
        _raise_routing("reconciliation_presence")


def _strict_load_reconciliation(loader: ReconciliationLoader, path: Path) -> object:
    try:
        reconciliation = loader(path)
    except _KNOWN_RECONCILIATION_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if type(reconciliation) is not ExternalPublicationExecutionReconciliation:
        _raise_routing("reconciliation_contract")
    _reconstruct_model(
        ExternalPublicationExecutionReconciliation,
        reconciliation,
        "reconciliation_contract",
    )
    return reconciliation


def _reconciliation_digest_once(
    digest_function: ReconciliationDigestFunction, reconciliation: object
) -> str:
    try:
        digest = digest_function(reconciliation)  # type: ignore[arg-type]
    except _KNOWN_RECONCILIATION_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if not _is_sha256(digest):
        _raise_routing("reconciliation_digest")
    return digest


def _validate_durable_result(
    *,
    outcome: object,
    reconciliation_path: Path,
    reconciliation_loader: ReconciliationLoader,
    reconciliation_digest_function: ReconciliationDigestFunction,
) -> None:
    result_kind = outcome.result_kind  # type: ignore[union-attr]
    state = outcome.state  # type: ignore[union-attr]
    if result_kind == _NONE_RESULT_KIND:
        if state != _RECOVERY_REQUIRED_STATE:  # type: ignore[comparison-overlap]
            _raise_routing("route_contract")
        if outcome.result_sha256 is not None:  # type: ignore[union-attr]
            _raise_routing("route_contract")
        if _target_present(reconciliation_path):
            _raise_routing("reconciliation_presence")
        return

    if result_kind != _RECONCILIATION_RESULT_KIND:
        _raise_routing("route_contract")
    if not _target_present(reconciliation_path):
        _raise_routing("reconciliation_presence")

    reconciliation = _strict_load_reconciliation(
        reconciliation_loader, reconciliation_path
    )
    reconciliation_digest = _reconciliation_digest_once(
        reconciliation_digest_function, reconciliation
    )
    if reconciliation_digest != outcome.result_sha256:  # type: ignore[union-attr]
        _raise_routing("reconciliation_lineage")

    if reconciliation.status == _MATCHED_STATUS:  # type: ignore[union-attr]
        expected_state = _COMPLETED_STATE
    elif reconciliation.status == _LINEAGE_MISMATCH_STATUS:  # type: ignore[union-attr]
        expected_state = _RECOVERY_REQUIRED_STATE
    else:
        _raise_routing("reconciliation_contract")
    if state != expected_state:
        _raise_routing("reconciliation_lineage")


def _outcome_digest_once(
    digest_function: OutcomeDigestFunction, outcome: object
) -> str:
    try:
        digest = digest_function(outcome)  # type: ignore[arg-type]
    except _KNOWN_OUTCOME_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if not _is_sha256(digest):
        _raise_routing("outcome_digest")
    return digest


def _reconstruct_model(
    model_type: type[object], instance: object, classification: Classification
) -> None:
    if type(instance) is not model_type:
        _raise_routing(classification)
    try:
        _require_exact_instance_fields(instance, classification)
        values = {
            field.name: getattr(instance, field.name) for field in fields(model_type)
        }
        model_type(**values)  # type: ignore[operator]
    except ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingError:
        raise
    except Exception:
        _raise_routing(classification)


def _require_exact_instance_fields(
    instance: object, classification: Classification
) -> None:
    try:
        expected = {field.name for field in fields(type(instance))}
        if set(vars(instance)) != expected:
            _raise_routing(classification)
    except ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingError:
        raise
    except Exception:
        _raise_routing(classification)


def _validate_decision(decision: object) -> None:
    if (
        type(decision)
        is not ExternalPublicationRecoveryResumeDecisionPreparationReconciliationDecisionRequired
    ):
        _raise_routing("configuration")
    try:
        _require_exact_instance_fields(decision, "configuration")
        values = {
            field.name: getattr(decision, field.name)
            for field in fields(
                ExternalPublicationRecoveryResumeDecisionPreparationReconciliationDecisionRequired
            )
        }
    except ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingError:
        raise
    except Exception:
        _raise_routing("configuration")

    if (
        type(values["schema_version"]) is not str
        or values["schema_version"] != _DECISION_SCHEMA_VERSION
    ):
        _raise_routing("configuration")
    for field_name in (
        "decision_preparation_reconciliation_outcome_sha256",
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
            _raise_routing("configuration")
    if (
        type(values["source_operation"]) is not str
        or values["source_operation"] not in _SOURCE_OPERATIONS
    ):
        _raise_routing("configuration")
    if (
        type(values["previous_recovery_kind"]) is not str
        or values["previous_recovery_kind"] not in _RECOVERY_KINDS
    ):
        _raise_routing("configuration")
    if (
        values["previous_recovery_kind"] == _MISMATCH_RECOVERY_KIND
        and values["source_operation"] != _OPERATION
    ):
        _raise_routing("configuration")
    if (
        type(values["recovery_kind"]) is not str
        or values["recovery_kind"] not in _RECOVERY_KINDS
    ):
        _raise_routing("configuration")
    if type(values["operation"]) is not str or values["operation"] != _OPERATION:
        _raise_routing("configuration")
    if (
        type(values["result_kind"]) is not str
        or values["result_kind"] not in _RESULT_KINDS
    ):
        _raise_routing("configuration")
    if type(values["state"]) is not str or values["state"] != _DECISION_STATE:
        _raise_routing("configuration")

    if values["result_kind"] == _NONE_RESULT_KIND:
        if (
            values["result_sha256"] is not None
            or values["recovery_kind"] != _ALREADY_ACQUIRED_RECOVERY_KIND
        ):
            _raise_routing("configuration")
        return
    if not _is_sha256(values["result_sha256"]):
        _raise_routing("configuration")
    if values["recovery_kind"] != _MISMATCH_RECOVERY_KIND:
        _raise_routing("configuration")


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _raise_routing(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingCompatibilityError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationDecisionRequired",
    "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingCompatibilityError",
    "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingError",
    "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationOutcomeRoutingFailureDetail",
    "route_external_publication_recovery_resume_decision_preparation_reconciliation_outcome",
]
