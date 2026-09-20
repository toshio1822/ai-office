# ruff: noqa: E501

"""Same-invocation recovery-resume reconciliation handoff for Phase 306.

Phase 306 binds one exact runtime resume request to one durable Phase 303
start authorization before delegating the Phase 305 acquisition route.  Only
an exact ``fresh_start_acquired`` route from that same Phase 305 invocation
may reach the provider-free Phase 288 resume operation.  A
``recovery_required`` route is returned unchanged and stops.

This boundary owns no durable artifact, marker inspection, reconciliation
classification, retry, fallback, cleanup, or publication execution.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal, NoReturn

from .external_publication import (
    ExternalPublicationApproval,
    ExternalPublicationError,
    external_publication_approval_digest,
)
from .external_publication_execution_reconciliation import (
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationExecutionReconciliationError,
)
from .external_publication_execution_reconciliation_evidence import (
    ExternalPublicationExecutionReconciliationEvidenceError,
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
    run_external_publication_operation,
)
from .external_publication_operation_intent import (
    ExternalPublicationOperationIntentError,
)
from .external_publication_operation_start import (
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartAcquisition,
    ExternalPublicationOperationStartError,
    external_publication_operation_start_digest,
)
from .external_publication_recovery_resume_decision_preparation_start_acquisition_handoff import (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffError,
)
from .external_publication_recovery_resume_decision_preparation_start_acquisition_routing import (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingError,
    route_external_publication_recovery_resume_decision_preparation_start_acquisition,
)
from .external_publication_recovery_resume_decision_preparation_start_authorization import (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError,
    load_external_publication_recovery_resume_decision_preparation_start_authorization,
)

_HANDOFF_ERROR_MESSAGE = (
    "external publication recovery resume decision preparation reconciliation "
    "handoff is blocked"
)
_AUTHORIZATION_SCHEMA_VERSION = (
    "external-publication-recovery-resume-decision-preparation-start-authorization.v1"
)
_ROUTE_SCHEMA_VERSION = "external-publication-recovery-resume-decision-preparation-start-acquisition-route.v1"
_START_SCHEMA_VERSION = "external-publication-operation-start.v1"
_AUTHORIZATION_FILENAME_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-start-authorization-"
)
_FILENAME_SUFFIX = ".json"
_PATH_TYPE = type(Path())
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

Classification = Literal[
    "configuration",
    "path_type",
    "request_contract",
    "authorization_contract",
    "authorization_path",
    "request_lineage",
    "approval_digest",
    "route_contract",
    "route_lineage",
    "start_digest",
    "reconciliation_contract",
    "dependency_error",
]

AuthorizationLoader = Callable[[Path], object]
ApprovalDigestFunction = Callable[[ExternalPublicationApproval], object]
StartDigestFunction = Callable[[ExternalPublicationOperationStart], object]
Phase305Function = Callable[..., object]
Phase288Function = Callable[..., object]

_KNOWN_AUTHORIZATION_ERRORS = (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError,
)
_KNOWN_PHASE305_ERRORS = (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoutingError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionHandoffError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError,
    ExternalPublicationOperationIntentError,
    ExternalPublicationOperationStartError,
)
_KNOWN_PHASE288_ERRORS = (
    ExternalPublicationOperationError,
    ExternalPublicationError,
    ExternalPublicationExecutionReconciliationResumeError,
    ExternalPublicationExecutionReconciliationOrchestrationError,
    ExternalPublicationExecutionReconciliationError,
    ExternalPublicationExecutionReconciliationEvidenceError,
)


@dataclass(frozen=True)
class ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffFailureDetail:
    """Detail-safe classification for one Phase 306 failure."""

    classification: Classification


class ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffError(
    ValueError
):
    """Raised when the Phase 306 reconciliation handoff cannot proceed safely."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_HANDOFF_ERROR_MESSAGE)
        self.detail = ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffFailureDetail(
            classification
        )


class ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffCompatibilityError(
    ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffError
):
    """Raised when a Phase 306 input, route, lineage, or result is incompatible."""


def run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff(
    *,
    start_authorization_path: Path,
    request: ExternalPublicationResumeOperationRequest,
    authorization_loader: AuthorizationLoader = load_external_publication_recovery_resume_decision_preparation_start_authorization,
    approval_digest_function: ApprovalDigestFunction = external_publication_approval_digest,
    start_digest_function: StartDigestFunction = external_publication_operation_start_digest,
    phase305_function: Phase305Function = route_external_publication_recovery_resume_decision_preparation_start_acquisition,
    phase288_function: Phase288Function = run_external_publication_operation,
) -> (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute
    | ExternalPublicationExecutionReconciliation
):
    """Bind one exact resume request and hand it through Phase 305 once.

    The caller supplies only the exact Phase 303 authorization path and the
    exact Phase 288 resume request.  Request validation happens before any
    authorization load.  The authorization is then strict-loaded once and
    checked against its canonical path before the request approval digest is
    computed once.  Phase 305 is called once with only the caller path.

    Only the exact same-invocation ``fresh_start_acquired`` route reaches
    Phase 288, which receives the exact caller request positionally.  The
    exact Phase 288 reconciliation is returned unchanged.  A
    ``recovery_required`` route is returned unchanged without a Phase 288
    call.
    """
    _preflight(
        start_authorization_path=start_authorization_path,
        request=request,
        authorization_loader=authorization_loader,
        approval_digest_function=approval_digest_function,
        start_digest_function=start_digest_function,
        phase305_function=phase305_function,
        phase288_function=phase288_function,
    )

    authorization = _load_authorization(authorization_loader, start_authorization_path)
    _validate_authorization_path(start_authorization_path, authorization)

    approval_digest = _approval_digest_once(approval_digest_function, request.approval)
    if (
        approval_digest != authorization.publication_approval_sha256
        or request.approval.publication_plan_sha256
        != authorization.publication_plan_sha256
    ):
        _raise_handoff("request_lineage")

    route = _call_phase305(phase305_function, start_authorization_path)
    _validate_route(route)

    start = route.acquisition.start
    start_digest = _start_digest_once(start_digest_function, start)
    if start_digest != authorization.expected_operation_start_sha256:
        _raise_handoff("route_lineage")
    if (
        start.operation_intent_sha256 != authorization.operation_intent_sha256
        or start.publication_approval_sha256
        != authorization.publication_approval_sha256
        or start.publication_approval_sha256 != approval_digest
        or start.publication_plan_sha256 != authorization.publication_plan_sha256
        or start.publication_plan_sha256 != request.approval.publication_plan_sha256
        or start.operation != "resume"
        or start.state != "started"
    ):
        _raise_handoff("route_lineage")

    if route.route == "recovery_required":
        return route

    reconciliation = _call_phase288(phase288_function, request)
    _validate_reconciliation(reconciliation)
    return reconciliation  # type: ignore[return-value]


def _preflight(
    *,
    start_authorization_path: object,
    request: object,
    authorization_loader: object,
    approval_digest_function: object,
    start_digest_function: object,
    phase305_function: object,
    phase288_function: object,
) -> None:
    """Reject caller contracts before any loader or acquisition work."""
    if type(start_authorization_path) is not _PATH_TYPE:
        _raise_handoff("path_type")
    if type(request) is not ExternalPublicationResumeOperationRequest:
        _raise_handoff("request_contract")

    _require_exact_instance_fields(request, "request_contract")
    if (
        type(request.ledger_directory) is not _PATH_TYPE  # type: ignore[union-attr]
        or type(request.execution_evidence_path) is not _PATH_TYPE  # type: ignore[union-attr]
        or type(request.execution_reconciliation_evidence_path) is not _PATH_TYPE  # type: ignore[union-attr]
    ):
        _raise_handoff("path_type")
    if type(request.approval) is not ExternalPublicationApproval:  # type: ignore[union-attr]
        _raise_handoff("request_contract")
    _reconstruct_approval(request.approval)  # type: ignore[union-attr]

    if not all(
        callable(dependency)
        for dependency in (
            authorization_loader,
            approval_digest_function,
            start_digest_function,
            phase305_function,
            phase288_function,
        )
    ):
        _raise_handoff("configuration")


def _load_authorization(
    loader: AuthorizationLoader, path: Path
) -> ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization:
    """Strict-load Phase 303 exactly once and locally reconstruct it."""
    try:
        authorization = loader(path)
    except _KNOWN_AUTHORIZATION_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")

    if (
        type(authorization)
        is not ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization
    ):
        _raise_handoff("authorization_contract")
    try:
        _require_exact_instance_fields(authorization, "authorization_contract")
        values = {
            field.name: getattr(authorization, field.name)
            for field in fields(
                ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization
            )
        }
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization(**values)
    except ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError:
        _raise_handoff("authorization_contract")
    except Exception:
        _raise_handoff("authorization_contract")
    return authorization


def _validate_authorization_path(path: Path, authorization: object) -> None:
    expected = path.parent / (
        f"{_AUTHORIZATION_FILENAME_PREFIX}"
        f"{authorization.decision_preparation_intent_binding_sha256}"
        f"{_FILENAME_SUFFIX}"
    )  # type: ignore[union-attr]
    if path != expected:
        _raise_handoff("authorization_path")


def _reconstruct_approval(approval: object) -> None:
    """Validate the exact caller approval without replacing its identity."""
    try:
        _require_exact_instance_fields(approval, "request_contract")
        values = {
            field.name: getattr(approval, field.name)
            for field in fields(ExternalPublicationApproval)
        }
        ExternalPublicationApproval(**values)
    except ExternalPublicationError:
        raise
    except Exception:
        _raise_handoff("request_contract")


def _approval_digest_once(
    digest_function: ApprovalDigestFunction, approval: ExternalPublicationApproval
) -> str:
    try:
        digest = digest_function(approval)
    except ExternalPublicationError:
        raise
    except Exception:
        _raise_handoff("dependency_error")
    if not _is_sha256(digest):
        _raise_handoff("approval_digest")
    return digest


def _call_phase305(phase305_function: Phase305Function, path: Path) -> object:
    try:
        route = phase305_function(start_authorization_path=path)
    except _KNOWN_PHASE305_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")
    return route


def _validate_route(
    route: object,
) -> None:
    """Reconstruct route, acquisition, and start without replacing identities."""
    if (
        type(route)
        is not ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute
    ):
        _raise_handoff("route_contract")
    try:
        _require_exact_instance_fields(route, "route_contract")
        acquisition = route.acquisition  # type: ignore[union-attr]
        if type(acquisition) is not ExternalPublicationOperationStartAcquisition:
            _raise_handoff("route_contract")
        _require_exact_instance_fields(acquisition, "route_contract")
        start = acquisition.start
        if type(start) is not ExternalPublicationOperationStart:
            _raise_handoff("route_contract")
        _require_exact_instance_fields(start, "route_contract")

        start_values = {
            field.name: getattr(start, field.name)
            for field in fields(ExternalPublicationOperationStart)
        }
        ExternalPublicationOperationStart(**start_values)

        acquisition_values = {
            field.name: getattr(acquisition, field.name)
            for field in fields(ExternalPublicationOperationStartAcquisition)
        }
        ExternalPublicationOperationStartAcquisition(**acquisition_values)

        route_values = {
            field.name: getattr(route, field.name)
            for field in fields(
                ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute
            )
        }
        ExternalPublicationRecoveryResumeDecisionPreparationStartAcquisitionRoute(
            **route_values
        )
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffError
    ):
        raise
    except Exception:
        _raise_handoff("route_contract")

    if (
        type(route.schema_version) is not str  # type: ignore[union-attr]
        or route.schema_version != _ROUTE_SCHEMA_VERSION  # type: ignore[union-attr]
        or type(route.route) is not str  # type: ignore[union-attr]
        or route.route
        not in {  # type: ignore[union-attr]
            "fresh_start_acquired",
            "recovery_required",
        }
        or type(acquisition.status) is not str
        or acquisition.status not in {"acquired", "already_acquired"}
        or (acquisition.status == "acquired" and route.route != "fresh_start_acquired")
        or (
            acquisition.status == "already_acquired"
            and route.route != "recovery_required"
        )
    ):
        _raise_handoff("route_contract")


def _start_digest_once(
    digest_function: StartDigestFunction, start: ExternalPublicationOperationStart
) -> str:
    try:
        digest = digest_function(start)
    except ExternalPublicationOperationStartError:
        raise
    except Exception:
        _raise_handoff("dependency_error")
    if not _is_sha256(digest):
        _raise_handoff("start_digest")
    return digest


def _call_phase288(phase288_function: Phase288Function, request: object) -> object:
    try:
        return phase288_function(request)
    except _KNOWN_PHASE288_ERRORS:
        raise
    except Exception:
        _raise_handoff("dependency_error")


def _validate_reconciliation(result: object) -> None:
    """Reconstruct every public result field while retaining the result object."""
    if type(result) is not ExternalPublicationExecutionReconciliation:
        _raise_handoff("reconciliation_contract")
    try:
        _require_exact_instance_fields(result, "reconciliation_contract")
        values = {
            field.name: getattr(result, field.name)
            for field in fields(ExternalPublicationExecutionReconciliation)
        }
        ExternalPublicationExecutionReconciliation(**values)
    except Exception:
        _raise_handoff("reconciliation_contract")


def _require_exact_instance_fields(
    instance: object, classification: Classification
) -> None:
    try:
        field_names = {field.name for field in fields(type(instance))}
        if set(vars(instance)) != field_names:
            _raise_handoff(classification)
    except (
        ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffError
    ):
        raise
    except Exception:
        _raise_handoff(classification)


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _raise_handoff(classification: Classification) -> NoReturn:
    raise ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffCompatibilityError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffCompatibilityError",
    "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffError",
    "ExternalPublicationRecoveryResumeDecisionPreparationReconciliationHandoffFailureDetail",
    "run_external_publication_recovery_resume_decision_preparation_reconciliation_handoff",
]
