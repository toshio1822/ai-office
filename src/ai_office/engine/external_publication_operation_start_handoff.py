"""Same-invocation acquired-start handoff from Phase 290 into Phase 288.

Phase 291 is a thin, non-persistent execution handoff.  It owns the Phase 290
acquisition call itself so that only the invocation which receives the
newly-created durable ``acquired`` result may dispatch Phase 288.  A
caller-supplied or reconstructed acquisition object is deliberately not an
accepted authorization input.

Phase 291 owns no durable artifact of its own.  It never retries, never
converts ``fresh`` to ``resume``, never replays an ``already_acquired`` marker,
and never rolls back or repairs a successfully created start marker.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NoReturn

from .external_publication import (
    ExternalPublicationApproval,
    ExternalPublicationError,
    ExternalPublicationPlan,
    ExternalPublicationTarget,
    external_publication_approval_digest,
    external_publication_plan_digest,
    validate_external_publication_approval,
)
from .external_publication_execution import (
    ExternalPublicationExecutionError,
    ExternalPublicationExecutionResult,
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
    ExternalPublicationFreshOperationRequest,
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
    acquire_external_publication_operation_start,
)

_HANDOFF_ERROR_MESSAGE = "external publication operation start handoff is blocked"
_START_SCHEMA_VERSION = "external-publication-operation-start.v1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_ACQUISITION_STATUSES = frozenset({"acquired", "already_acquired"})
_OPERATIONS = frozenset({"fresh", "resume"})

Classification = Literal[
    "configuration",
    "path_type",
    "request_contract",
    "request_lineage",
    "start_contract",
    "start_lineage",
    "operation_mismatch",
    "fresh_result_contract",
    "resume_result_contract",
    "dependency_error",
]
Phase290Function = Callable[..., object]
Phase288Function = Callable[..., object]


@dataclass(frozen=True)
class ExternalPublicationOperationStartHandoffFailureDetail:
    """Detail-safe classification for one Phase 291 handoff failure."""

    classification: Classification


class ExternalPublicationOperationStartHandoffError(ValueError):
    """Raised when the start handoff cannot safely proceed."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_HANDOFF_ERROR_MESSAGE)
        self.detail = ExternalPublicationOperationStartHandoffFailureDetail(
            classification
        )


class ExternalPublicationOperationStartHandoffCompatibilityError(
    ExternalPublicationOperationStartHandoffError
):
    """Raised when a Phase 291 request, dependency, or result is incompatible."""


def run_external_publication_operation_start_handoff(
    *,
    intent_path: Path,
    start_path: Path,
    request: (
        ExternalPublicationFreshOperationRequest
        | ExternalPublicationResumeOperationRequest
    ),
    phase290_function: Phase290Function = (
        acquire_external_publication_operation_start
    ),
    phase288_function: Phase288Function = run_external_publication_operation,
) -> (
    ExternalPublicationOperationStartAcquisition
    | ExternalPublicationExecutionResult
    | ExternalPublicationExecutionReconciliation
):
    """Hand off one preflighted request across the Phase 290 start fence.

    The request is preflighted before the irreversible acquisition, Phase 290
    is called exactly once by this function, and Phase 288 is dispatched only
    from the exact same-invocation ``acquired`` result.  ``already_acquired``
    stops with zero Phase 288 calls and returns the identical acquisition
    object.
    """
    expected_operation, approval_digest, plan_binding_digest = _preflight_request(
        intent_path=intent_path,
        start_path=start_path,
        request=request,
        phase290_function=phase290_function,
        phase288_function=phase288_function,
    )

    acquisition = _acquire_start(
        phase290_function=phase290_function,
        intent_path=intent_path,
        start_path=start_path,
    )
    _validate_acquisition_contract(acquisition)
    start = acquisition.start
    _validate_start_contract(start)
    _validate_start_lineage(
        start,
        expected_operation=expected_operation,
        approval_digest=approval_digest,
        plan_binding_digest=plan_binding_digest,
    )

    if acquisition.status == "already_acquired":
        return acquisition
    return _dispatch_phase288(
        phase288_function=phase288_function,
        request=request,
        expected_operation=expected_operation,
    )


def _preflight_request(
    *,
    intent_path: object,
    start_path: object,
    request: object,
    phase290_function: object,
    phase288_function: object,
) -> tuple[str, str, str]:
    if type(intent_path) is not _PATH_TYPE or type(start_path) is not _PATH_TYPE:
        _raise_handoff("path_type")
    if not callable(phase290_function) or not callable(phase288_function):
        _raise_handoff("configuration")

    if type(request) is ExternalPublicationFreshOperationRequest:
        return _preflight_fresh_request(request)
    if type(request) is ExternalPublicationResumeOperationRequest:
        return _preflight_resume_request(request)
    _raise_handoff("request_contract")


def _preflight_fresh_request(
    request: ExternalPublicationFreshOperationRequest,
) -> tuple[str, str, str]:
    if (
        type(request.execution_evidence_path) is not _PATH_TYPE
        or type(request.plan_reconciliation_evidence_path) is not _PATH_TYPE
        or type(request.output_path) is not _PATH_TYPE
        or type(request.ledger_directory) is not _PATH_TYPE
    ):
        _raise_handoff("path_type")
    if type(request.plan) is not ExternalPublicationPlan:
        _raise_handoff("request_contract")
    if type(request.approval) is not ExternalPublicationApproval:
        _raise_handoff("request_contract")
    if type(request.target) is not ExternalPublicationTarget:
        _raise_handoff("request_contract")
    if not callable(request.transport):
        _raise_handoff("request_contract")

    try:
        validate_external_publication_approval(request.plan, request.approval)
    except ExternalPublicationError:
        raise
    except Exception:
        _raise_handoff("dependency_error")

    approval_digest = _approval_digest_once(request.approval)
    plan_binding_digest = _plan_digest_once(request.plan)
    if not _is_sha256(approval_digest) or not _is_sha256(plan_binding_digest):
        _raise_handoff("request_lineage")
    return "fresh", approval_digest, plan_binding_digest


def _preflight_resume_request(
    request: ExternalPublicationResumeOperationRequest,
) -> tuple[str, str, str]:
    if (
        type(request.ledger_directory) is not _PATH_TYPE
        or type(request.execution_evidence_path) is not _PATH_TYPE
        or type(request.execution_reconciliation_evidence_path) is not _PATH_TYPE
    ):
        _raise_handoff("path_type")
    if type(request.approval) is not ExternalPublicationApproval:
        _raise_handoff("request_contract")

    approval_digest = _approval_digest_once(request.approval)
    plan_binding_digest = request.approval.publication_plan_sha256
    if not _is_sha256(approval_digest) or not _is_sha256(plan_binding_digest):
        _raise_handoff("request_lineage")
    return "resume", approval_digest, plan_binding_digest


def _approval_digest_once(approval: ExternalPublicationApproval) -> object:
    try:
        return external_publication_approval_digest(approval)
    except ExternalPublicationError:
        raise
    except Exception:
        _raise_handoff("dependency_error")


def _plan_digest_once(plan: ExternalPublicationPlan) -> object:
    try:
        return external_publication_plan_digest(plan)
    except ExternalPublicationError:
        raise
    except Exception:
        _raise_handoff("dependency_error")


def _acquire_start(
    *,
    phase290_function: Phase290Function,
    intent_path: Path,
    start_path: Path,
) -> object:
    try:
        return phase290_function(intent_path=intent_path, start_path=start_path)
    except (
        ExternalPublicationOperationStartError,
        ExternalPublicationOperationIntentError,
        ExternalPublicationError,
    ):
        raise
    except Exception:
        _raise_handoff("dependency_error")


def _validate_acquisition_contract(acquisition: object) -> None:
    if type(acquisition) is not ExternalPublicationOperationStartAcquisition:
        _raise_handoff("start_contract")
    if (
        type(acquisition.status) is not str  # type: ignore[union-attr]
        or acquisition.status not in _ACQUISITION_STATUSES  # type: ignore[union-attr]
    ):
        _raise_handoff("start_contract")
    if type(acquisition.start) is not ExternalPublicationOperationStart:  # type: ignore[union-attr]
        _raise_handoff("start_contract")


def _validate_start_contract(start: object) -> None:
    if type(start) is not ExternalPublicationOperationStart:
        _raise_handoff("start_contract")
    if (
        type(start.schema_version) is not str  # type: ignore[union-attr]
        or start.schema_version != _START_SCHEMA_VERSION  # type: ignore[union-attr]
    ):
        _raise_handoff("start_contract")
    if not _is_sha256(start.operation_intent_sha256):  # type: ignore[union-attr]
        _raise_handoff("start_contract")
    if not _is_sha256(start.publication_approval_sha256):  # type: ignore[union-attr]
        _raise_handoff("start_contract")
    if not _is_sha256(start.publication_plan_sha256):  # type: ignore[union-attr]
        _raise_handoff("start_contract")
    if (
        type(start.operation) is not str  # type: ignore[union-attr]
        or start.operation not in _OPERATIONS  # type: ignore[union-attr]
    ):
        _raise_handoff("start_contract")
    if (
        type(start.state) is not str  # type: ignore[union-attr]
        or start.state != "started"  # type: ignore[union-attr]
    ):
        _raise_handoff("start_contract")


def _validate_start_lineage(
    start: ExternalPublicationOperationStart,
    *,
    expected_operation: str,
    approval_digest: str,
    plan_binding_digest: str,
) -> None:
    if start.operation != expected_operation:
        _raise_handoff("operation_mismatch")
    if start.publication_approval_sha256 != approval_digest:
        _raise_handoff("start_lineage")
    if start.publication_plan_sha256 != plan_binding_digest:
        _raise_handoff("start_lineage")
    if not _is_sha256(start.operation_intent_sha256):
        _raise_handoff("start_lineage")


def _dispatch_phase288(
    *,
    phase288_function: Phase288Function,
    request: object,
    expected_operation: str,
) -> object:
    try:
        result = phase288_function(request)
    except (
        ExternalPublicationOperationError,
        ExternalPublicationError,
        ExternalPublicationExecutionOrchestrationError,
        ExternalPublicationExecutionError,
        ExternalPublicationExecutionEvidenceError,
        ExternalPublicationExecutionReconciliationResumeError,
        ExternalPublicationExecutionReconciliationOrchestrationError,
        ExternalPublicationExecutionReconciliationError,
        ExternalPublicationExecutionReconciliationEvidenceError,
    ):
        raise
    except Exception:
        _raise_handoff("dependency_error")

    if expected_operation == "fresh":
        _validate_fresh_result(result)
    else:
        _validate_resume_result(result)
    return result


def _validate_fresh_result(result: object) -> None:
    if type(result) is not ExternalPublicationExecutionResult:
        _raise_handoff("fresh_result_contract")
    try:
        ExternalPublicationExecutionResult(
            schema_version=result.schema_version,  # type: ignore[union-attr]
            regeneration_id=result.regeneration_id,  # type: ignore[union-attr]
            publication_attempt_claim_sha256=(  # type: ignore[union-attr]
                result.publication_attempt_claim_sha256
            ),
            publication_plan_sha256=(  # type: ignore[union-attr]
                result.publication_plan_sha256
            ),
            publication_approval_sha256=(  # type: ignore[union-attr]
                result.publication_approval_sha256
            ),
            business_output_sha256=result.business_output_sha256,  # type: ignore[union-attr]
            output_byte_length=result.output_byte_length,  # type: ignore[union-attr]
            provider=result.provider,  # type: ignore[union-attr]
            publication_target_sha256=(  # type: ignore[union-attr]
                result.publication_target_sha256
            ),
            publication_id=result.publication_id,  # type: ignore[union-attr]
            status=result.status,  # type: ignore[union-attr]
        )
    except Exception:
        _raise_handoff("fresh_result_contract")


def _validate_resume_result(result: object) -> None:
    if type(result) is not ExternalPublicationExecutionReconciliation:
        _raise_handoff("resume_result_contract")
    try:
        ExternalPublicationExecutionReconciliation(
            schema_version=result.schema_version,  # type: ignore[union-attr]
            claim_sha256=result.claim_sha256,  # type: ignore[union-attr]
            execution_evidence_sha256=(  # type: ignore[union-attr]
                result.execution_evidence_sha256
            ),
            status=result.status,  # type: ignore[union-attr]
            mismatched_fields=result.mismatched_fields,  # type: ignore[union-attr]
        )
    except Exception:
        _raise_handoff("resume_result_contract")


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _raise_handoff(classification: Classification) -> NoReturn:
    raise ExternalPublicationOperationStartHandoffCompatibilityError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationOperationStartHandoffCompatibilityError",
    "ExternalPublicationOperationStartHandoffError",
    "ExternalPublicationOperationStartHandoffFailureDetail",
    "run_external_publication_operation_start_handoff",
]
