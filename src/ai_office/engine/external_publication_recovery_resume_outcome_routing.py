"""Read-only recovery resume outcome routing boundary (Phase 299).

Phase 299 adds one strict, read-only **recovery resume outcome routing
boundary** above Phase 298.  It consumes only the durable recovery lineage,
derives every canonical artifact path internally, and routes the exact Phase
298 outcome:

    completed / reconciliation                     -> exact Phase 298 outcome
                                                      unchanged; terminal stop
    recovery_required / reconciliation             -> decision required
                                                      (current reconciliation_mismatch)
    recovery_required / none                       -> decision required
                                                      (current already_acquired)

The Phase 298 field ``recovery_kind`` is **historical provenance**: it records
the recovery reason that authorized the resume attempt whose outcome Phase 298
persisted.  It is not necessarily the reason a new recovery decision is now
required.  Phase 299 therefore preserves it separately as
``previous_recovery_kind`` and derives the current ``recovery_kind`` only from
the current Phase 298 state and result kind.

Phase 299 performs no execution and no persistence.  It calls no Phase 298,
297, 293, 291, or 290 orchestration, accepts no runtime request, provider,
transport, credential, operator identity, or human decision, and adds no
retry, fallback, or automatic continuation.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal, NoReturn

from .external_publication_operation_start import (
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartError,
    external_publication_operation_start_digest,
    load_external_publication_operation_start,
)
from .external_publication_recovery_resume_intent_binding import (
    ExternalPublicationRecoveryResumeIntentBinding,
    ExternalPublicationRecoveryResumeIntentBindingError,
    external_publication_recovery_resume_intent_binding_digest,
    load_external_publication_recovery_resume_intent_binding,
)
from .external_publication_recovery_resume_outcome import (
    ExternalPublicationRecoveryResumeOutcome,
    ExternalPublicationRecoveryResumeOutcomeError,
    external_publication_recovery_resume_outcome_digest,
    load_external_publication_recovery_resume_outcome,
)
from .external_publication_recovery_resume_start_authorization import (
    ExternalPublicationRecoveryResumeStartAuthorization,
    ExternalPublicationRecoveryResumeStartAuthorizationError,
    external_publication_recovery_resume_start_authorization_digest,
    load_external_publication_recovery_resume_start_authorization,
)

_ROUTING_ERROR_MESSAGE = (
    "external publication recovery resume outcome routing is blocked"
)
_DECISION_SCHEMA_VERSION = "external-publication-recovery-resume-decision-required.v1"
_AUTHORIZATION_FILENAME_PREFIX = (
    "external-publication-recovery-resume-start-authorization-"
)
_START_FILENAME_PREFIX = "external-publication-recovery-resume-start-"
_OUTCOME_FILENAME_PREFIX = "external-publication-recovery-resume-outcome-"
_FILENAME_SUFFIX = ".json"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_SOURCE_OPERATIONS = frozenset({"fresh", "resume"})
_RECOVERY_KINDS = frozenset({"already_acquired", "reconciliation_mismatch"})
_RESULT_KINDS = frozenset({"reconciliation", "none"})
_COMPLETED_STATE = "completed"
_RECOVERY_REQUIRED_STATE = "recovery_required"
_MISMATCH_RECOVERY_KIND = "reconciliation_mismatch"
_ALREADY_ACQUIRED_RECOVERY_KIND = "already_acquired"
_RECONCILIATION_RESULT_KIND = "reconciliation"
_NONE_RESULT_KIND = "none"
_OPERATION = "resume"
_DECISION_STATE = "decision_required"
_START_STATE = "started"

Classification = Literal[
    "configuration",
    "path_type",
    "binding_contract",
    "binding_digest",
    "authorization_contract",
    "authorization_digest",
    "authorization_lineage",
    "outcome_contract",
    "outcome_digest",
    "outcome_lineage",
    "start_contract",
    "start_digest",
    "start_lineage",
    "route_contract",
    "dependency_error",
]

_KNOWN_PREDECESSOR_ERRORS = (
    ExternalPublicationRecoveryResumeIntentBindingError,
    ExternalPublicationRecoveryResumeStartAuthorizationError,
    ExternalPublicationRecoveryResumeOutcomeError,
    ExternalPublicationOperationStartError,
)


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeOutcomeRoutingFailureDetail:
    """Detail-safe classification for one routing failure."""

    classification: Classification


class ExternalPublicationRecoveryResumeOutcomeRoutingError(ValueError):
    """Raised when a routing input, lineage, or dependency is not exact."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_ROUTING_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeOutcomeRoutingFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError(
    ExternalPublicationRecoveryResumeOutcomeRoutingError
):
    """Raised when a path, model, or lineage is incompatible."""


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeDecisionRequired:
    """Immutable, secret-free in-memory recovery resume decision-required.

    ``state`` ``"decision_required"`` records only that the exact Phase 298
    outcome requires one explicit future recovery decision.  It records no
    decision, no authorization, no execution, and no replay permission.
    ``previous_recovery_kind`` is the Phase 298 historical provenance, while
    ``recovery_kind`` is the current reason derived only from the current Phase
    298 state and result kind.  This object is not persisted in Phase 299.
    """

    schema_version: Literal["external-publication-recovery-resume-decision-required.v1"]
    recovery_resume_outcome_sha256: str
    resume_start_authorization_sha256: str
    resume_intent_binding_sha256: str
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


BindingLoader = Callable[[Path], object]
BindingDigestFunction = Callable[
    [ExternalPublicationRecoveryResumeIntentBinding], object
]
AuthorizationLoader = Callable[[Path], object]
AuthorizationDigestFunction = Callable[
    [ExternalPublicationRecoveryResumeStartAuthorization], object
]
OutcomeLoader = Callable[[Path], object]
OutcomeDigestFunction = Callable[[ExternalPublicationRecoveryResumeOutcome], object]
StartLoader = Callable[[Path], object]
StartDigestFunction = Callable[[ExternalPublicationOperationStart], object]


def route_external_publication_recovery_resume_outcome(
    *,
    resume_intent_binding_path: Path,
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
    outcome_loader: OutcomeLoader = load_external_publication_recovery_resume_outcome,
    outcome_digest_function: OutcomeDigestFunction = (
        external_publication_recovery_resume_outcome_digest
    ),
    start_loader: StartLoader = load_external_publication_operation_start,
    start_digest_function: StartDigestFunction = (
        external_publication_operation_start_digest
    ),
) -> ExternalPublicationRecoveryResumeOutcome | (
    ExternalPublicationRecoveryResumeDecisionRequired
):
    """Route one exact durable Phase 298 recovery resume outcome, read-only.

    The caller supplies only the exact Phase 295 binding path.  Every other
    canonical path is derived from the exact binding parent and the internally
    computed digests.  No artifact is created, modified, or deleted.
    """
    _preflight(
        resume_intent_binding_path=resume_intent_binding_path,
        binding_loader=binding_loader,
        binding_digest_function=binding_digest_function,
        authorization_loader=authorization_loader,
        authorization_digest_function=authorization_digest_function,
        outcome_loader=outcome_loader,
        outcome_digest_function=outcome_digest_function,
        start_loader=start_loader,
        start_digest_function=start_digest_function,
    )

    binding = _strict_load_binding(binding_loader, resume_intent_binding_path)
    binding_digest = _binding_digest_once(binding_digest_function, binding)

    authorization_path = _derive_authorization_path(
        resume_intent_binding_path, binding_digest
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

    outcome_path = _derive_outcome_path(
        resume_intent_binding_path, authorization_digest
    )
    outcome = _strict_load_outcome(outcome_loader, outcome_path)
    _validate_outcome_lineage(
        binding=binding,
        authorization=authorization,
        outcome=outcome,
        binding_digest=binding_digest,
        authorization_digest=authorization_digest,
    )

    start_path = _derive_start_path(resume_intent_binding_path, authorization_digest)
    start = _strict_load_start(start_loader, start_path)
    start_digest = _start_digest_once(start_digest_function, start)
    _validate_start_lineage(
        authorization=authorization,
        outcome=outcome,
        start=start,
        start_digest=start_digest,
    )

    return _route(
        outcome=outcome,
        authorization=authorization,
        binding_digest=binding_digest,
        authorization_digest=authorization_digest,
        start_digest=start_digest,
        outcome_digest_function=outcome_digest_function,
    )


def _preflight(
    *,
    resume_intent_binding_path: object,
    binding_loader: object,
    binding_digest_function: object,
    authorization_loader: object,
    authorization_digest_function: object,
    outcome_loader: object,
    outcome_digest_function: object,
    start_loader: object,
    start_digest_function: object,
) -> None:
    if type(resume_intent_binding_path) is not _PATH_TYPE:
        _raise_routing("path_type")
    if not callable(binding_loader):
        _raise_routing("configuration")
    if not callable(binding_digest_function):
        _raise_routing("configuration")
    if not callable(authorization_loader):
        _raise_routing("configuration")
    if not callable(authorization_digest_function):
        _raise_routing("configuration")
    if not callable(outcome_loader):
        _raise_routing("configuration")
    if not callable(outcome_digest_function):
        _raise_routing("configuration")
    if not callable(start_loader):
        _raise_routing("configuration")
    if not callable(start_digest_function):
        _raise_routing("configuration")


def _reconstruct(
    model_type: type[object],
    instance: object,
    classification: Classification,
) -> None:
    """Independently revalidate one predecessor model through its own rules.

    Rebuilding the exact public model from the loaded fields re-runs the
    predecessor's own ``__post_init__`` validation, so duplicate members,
    canonical member order, and status/collection coupling are all enforced
    without trusting the predecessor's private helpers.
    """
    try:
        values = {
            field.name: getattr(instance, field.name)
            for field in fields(model_type)  # type: ignore[arg-type]
        }
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_routing(classification)
    try:
        model_type(**values)  # type: ignore[operator]
    except _KNOWN_PREDECESSOR_ERRORS:
        _raise_routing(classification)
    except Exception:
        _raise_routing(classification)


def _strict_load_binding(loader: BindingLoader, path: Path) -> object:
    try:
        binding = loader(path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if type(binding) is not ExternalPublicationRecoveryResumeIntentBinding:
        _raise_routing("binding_contract")
    _reconstruct(
        ExternalPublicationRecoveryResumeIntentBinding, binding, "binding_contract"
    )
    return binding


def _binding_digest_once(
    digest_function: BindingDigestFunction, binding: object
) -> str:
    try:
        digest = digest_function(binding)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if not _is_sha256(digest):
        _raise_routing("binding_digest")
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


def _strict_load_authorization(loader: AuthorizationLoader, path: Path) -> object:
    try:
        authorization = loader(path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if type(authorization) is not ExternalPublicationRecoveryResumeStartAuthorization:
        _raise_routing("authorization_contract")
    _reconstruct(
        ExternalPublicationRecoveryResumeStartAuthorization,
        authorization,
        "authorization_contract",
    )
    return authorization


def _authorization_digest_once(
    digest_function: AuthorizationDigestFunction, authorization: object
) -> str:
    try:
        digest = digest_function(authorization)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if not _is_sha256(digest):
        _raise_routing("authorization_digest")
    return digest  # type: ignore[return-value]


def _validate_authorization_lineage(
    *,
    binding: object,
    authorization: object,
    binding_digest: str,
) -> None:
    if (
        authorization.resume_intent_binding_sha256  # type: ignore[union-attr]
        != binding_digest
    ):
        _raise_routing("authorization_lineage")
    if (
        authorization.resume_preparation_sha256  # type: ignore[union-attr]
        != binding.resume_preparation_sha256  # type: ignore[union-attr]
    ):
        _raise_routing("authorization_lineage")
    if (
        authorization.recovery_decision_sha256  # type: ignore[union-attr]
        != binding.recovery_decision_sha256  # type: ignore[union-attr]
    ):
        _raise_routing("authorization_lineage")
    if (
        authorization.operation_intent_sha256  # type: ignore[union-attr]
        != binding.operation_intent_sha256  # type: ignore[union-attr]
    ):
        _raise_routing("authorization_lineage")
    if (
        authorization.publication_approval_sha256  # type: ignore[union-attr]
        != binding.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_routing("authorization_lineage")
    if (
        authorization.publication_plan_sha256  # type: ignore[union-attr]
        != binding.publication_plan_sha256  # type: ignore[union-attr]
    ):
        _raise_routing("authorization_lineage")
    if (
        authorization.source_operation  # type: ignore[union-attr]
        != binding.source_operation  # type: ignore[union-attr]
    ):
        _raise_routing("authorization_lineage")
    if (
        authorization.recovery_kind  # type: ignore[union-attr]
        != binding.recovery_kind  # type: ignore[union-attr]
    ):
        _raise_routing("authorization_lineage")
    if (
        authorization.operation  # type: ignore[union-attr]
        != binding.operation  # type: ignore[union-attr]
    ):
        _raise_routing("authorization_lineage")
    if authorization.operation != _OPERATION:  # type: ignore[union-attr]
        _raise_routing("authorization_lineage")
    if authorization.state != "authorized":  # type: ignore[union-attr]
        _raise_routing("authorization_lineage")


def _strict_load_outcome(loader: OutcomeLoader, path: Path) -> object:
    try:
        outcome = loader(path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if type(outcome) is not ExternalPublicationRecoveryResumeOutcome:
        _raise_routing("outcome_contract")
    _reconstruct(ExternalPublicationRecoveryResumeOutcome, outcome, "outcome_contract")
    return outcome


def _validate_outcome_lineage(
    *,
    binding: object,
    authorization: object,
    outcome: object,
    binding_digest: str,
    authorization_digest: str,
) -> None:
    if (
        outcome.resume_start_authorization_sha256  # type: ignore[union-attr]
        != authorization_digest
    ):
        _raise_routing("outcome_lineage")
    if (
        outcome.resume_intent_binding_sha256  # type: ignore[union-attr]
        != binding_digest
    ):
        _raise_routing("outcome_lineage")
    if (
        outcome.operation_intent_sha256  # type: ignore[union-attr]
        != authorization.operation_intent_sha256  # type: ignore[union-attr]
    ):
        _raise_routing("outcome_lineage")
    if (
        outcome.operation_intent_sha256  # type: ignore[union-attr]
        != binding.operation_intent_sha256  # type: ignore[union-attr]
    ):
        _raise_routing("outcome_lineage")
    if (
        outcome.operation_start_sha256  # type: ignore[union-attr]
        != authorization.expected_operation_start_sha256  # type: ignore[union-attr]
    ):
        _raise_routing("outcome_lineage")
    if (
        outcome.publication_approval_sha256  # type: ignore[union-attr]
        != authorization.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_routing("outcome_lineage")
    if (
        outcome.publication_plan_sha256  # type: ignore[union-attr]
        != authorization.publication_plan_sha256  # type: ignore[union-attr]
    ):
        _raise_routing("outcome_lineage")
    if (
        outcome.source_operation  # type: ignore[union-attr]
        != authorization.source_operation  # type: ignore[union-attr]
    ):
        _raise_routing("outcome_lineage")
    if (
        outcome.recovery_kind  # type: ignore[union-attr]
        != authorization.recovery_kind  # type: ignore[union-attr]
    ):
        _raise_routing("outcome_lineage")
    if outcome.operation != _OPERATION:  # type: ignore[union-attr]
        _raise_routing("outcome_lineage")


def _strict_load_start(loader: StartLoader, path: Path) -> object:
    try:
        start = loader(path)
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if type(start) is not ExternalPublicationOperationStart:
        _raise_routing("start_contract")
    _reconstruct(ExternalPublicationOperationStart, start, "start_contract")
    return start


def _start_digest_once(digest_function: StartDigestFunction, start: object) -> str:
    try:
        digest = digest_function(start)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if not _is_sha256(digest):
        _raise_routing("start_digest")
    return digest  # type: ignore[return-value]


def _validate_start_lineage(
    *,
    authorization: object,
    outcome: object,
    start: object,
    start_digest: str,
) -> None:
    if start_digest != authorization.expected_operation_start_sha256:  # type: ignore[union-attr]
        _raise_routing("start_lineage")
    if start_digest != outcome.operation_start_sha256:  # type: ignore[union-attr]
        _raise_routing("start_lineage")
    if (
        start.operation_intent_sha256  # type: ignore[union-attr]
        != authorization.operation_intent_sha256  # type: ignore[union-attr]
    ):
        _raise_routing("start_lineage")
    if (
        start.operation_intent_sha256  # type: ignore[union-attr]
        != outcome.operation_intent_sha256  # type: ignore[union-attr]
    ):
        _raise_routing("start_lineage")
    if (
        start.publication_approval_sha256  # type: ignore[union-attr]
        != authorization.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_routing("start_lineage")
    if (
        start.publication_approval_sha256  # type: ignore[union-attr]
        != outcome.publication_approval_sha256  # type: ignore[union-attr]
    ):
        _raise_routing("start_lineage")
    if (
        start.publication_plan_sha256  # type: ignore[union-attr]
        != authorization.publication_plan_sha256  # type: ignore[union-attr]
    ):
        _raise_routing("start_lineage")
    if (
        start.publication_plan_sha256  # type: ignore[union-attr]
        != outcome.publication_plan_sha256  # type: ignore[union-attr]
    ):
        _raise_routing("start_lineage")
    if start.operation != _OPERATION:  # type: ignore[union-attr]
        _raise_routing("start_lineage")
    if start.state != _START_STATE:  # type: ignore[union-attr]
        _raise_routing("start_lineage")


def _route(
    *,
    outcome: object,
    authorization: object,
    binding_digest: str,
    authorization_digest: str,
    start_digest: str,
    outcome_digest_function: OutcomeDigestFunction,
) -> ExternalPublicationRecoveryResumeOutcome | (
    ExternalPublicationRecoveryResumeDecisionRequired
):
    state = outcome.state  # type: ignore[union-attr]
    result_kind = outcome.result_kind  # type: ignore[union-attr]

    if state == _COMPLETED_STATE:
        return outcome  # type: ignore[return-value]

    if state != _RECOVERY_REQUIRED_STATE:
        _raise_routing("route_contract")

    if result_kind == _RECONCILIATION_RESULT_KIND:
        recovery_kind = _MISMATCH_RECOVERY_KIND
    elif result_kind == _NONE_RESULT_KIND:
        recovery_kind = _ALREADY_ACQUIRED_RECOVERY_KIND
    else:
        _raise_routing("route_contract")

    outcome_digest = _outcome_digest_once(outcome_digest_function, outcome)

    decision = ExternalPublicationRecoveryResumeDecisionRequired(
        schema_version=_DECISION_SCHEMA_VERSION,
        recovery_resume_outcome_sha256=outcome_digest,
        resume_start_authorization_sha256=authorization_digest,
        resume_intent_binding_sha256=binding_digest,
        operation_intent_sha256=outcome.operation_intent_sha256,  # type: ignore[union-attr]
        operation_start_sha256=start_digest,
        publication_approval_sha256=outcome.publication_approval_sha256,  # type: ignore[union-attr]
        publication_plan_sha256=outcome.publication_plan_sha256,  # type: ignore[union-attr]
        source_operation=outcome.source_operation,  # type: ignore[union-attr]
        previous_recovery_kind=outcome.recovery_kind,  # type: ignore[union-attr]
        recovery_kind=recovery_kind,
        operation=_OPERATION,
        result_kind=outcome.result_kind,  # type: ignore[union-attr]
        result_sha256=outcome.result_sha256,  # type: ignore[union-attr]
        state=_DECISION_STATE,
    )
    return decision


def _outcome_digest_once(
    digest_function: OutcomeDigestFunction, outcome: object
) -> str:
    try:
        digest = digest_function(outcome)  # type: ignore[arg-type]
    except _KNOWN_PREDECESSOR_ERRORS:
        raise
    except Exception:
        _raise_routing("dependency_error")
    if not _is_sha256(digest):
        _raise_routing("outcome_digest")
    return digest  # type: ignore[return-value]


def _validate_decision(decision: object) -> None:
    if type(decision) is not ExternalPublicationRecoveryResumeDecisionRequired:
        _raise_routing("configuration")
    try:
        schema_version = decision.schema_version  # type: ignore[union-attr]
        outcome_digest = decision.recovery_resume_outcome_sha256  # type: ignore[union-attr]
        authorization_digest = (
            decision.resume_start_authorization_sha256  # type: ignore[union-attr]
        )
        binding_digest = decision.resume_intent_binding_sha256  # type: ignore[union-attr]
        intent_digest = decision.operation_intent_sha256  # type: ignore[union-attr]
        start_digest = decision.operation_start_sha256  # type: ignore[union-attr]
        approval_digest = decision.publication_approval_sha256  # type: ignore[union-attr]
        plan_digest = decision.publication_plan_sha256  # type: ignore[union-attr]
        source_operation = decision.source_operation  # type: ignore[union-attr]
        previous_recovery_kind = (
            decision.previous_recovery_kind  # type: ignore[union-attr]
        )
        recovery_kind = decision.recovery_kind  # type: ignore[union-attr]
        operation = decision.operation  # type: ignore[union-attr]
        result_kind = decision.result_kind  # type: ignore[union-attr]
        result_digest = decision.result_sha256  # type: ignore[union-attr]
        state = decision.state  # type: ignore[union-attr]
    except Exception:
        _raise_routing("configuration")

    if type(schema_version) is not str or schema_version != _DECISION_SCHEMA_VERSION:
        _raise_routing("configuration")
    for digest in (
        outcome_digest,
        authorization_digest,
        binding_digest,
        intent_digest,
        start_digest,
        approval_digest,
        plan_digest,
    ):
        if not _is_sha256(digest):
            _raise_routing("configuration")
    if type(source_operation) is not str or source_operation not in _SOURCE_OPERATIONS:
        _raise_routing("configuration")
    if (
        type(previous_recovery_kind) is not str
        or previous_recovery_kind not in _RECOVERY_KINDS
    ):
        _raise_routing("configuration")
    if previous_recovery_kind == _MISMATCH_RECOVERY_KIND and source_operation != (
        _OPERATION
    ):
        _raise_routing("configuration")
    if type(recovery_kind) is not str or recovery_kind not in _RECOVERY_KINDS:
        _raise_routing("configuration")
    if type(operation) is not str or operation != _OPERATION:
        _raise_routing("configuration")
    if type(result_kind) is not str or result_kind not in _RESULT_KINDS:
        _raise_routing("configuration")
    if type(state) is not str or state != _DECISION_STATE:
        _raise_routing("configuration")

    if result_kind == _NONE_RESULT_KIND:
        if result_digest is not None:
            _raise_routing("configuration")
        if recovery_kind != _ALREADY_ACQUIRED_RECOVERY_KIND:
            _raise_routing("configuration")
        return
    if not _is_sha256(result_digest):
        _raise_routing("configuration")
    if recovery_kind != _MISMATCH_RECOVERY_KIND:
        _raise_routing("configuration")


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _raise_routing(
    classification: Classification,
) -> NoReturn:
    raise ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationRecoveryResumeDecisionRequired",
    "ExternalPublicationRecoveryResumeOutcomeRoutingCompatibilityError",
    "ExternalPublicationRecoveryResumeOutcomeRoutingError",
    "ExternalPublicationRecoveryResumeOutcomeRoutingFailureDetail",
    "route_external_publication_recovery_resume_outcome",
]
