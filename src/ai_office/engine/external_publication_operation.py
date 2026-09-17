"""Explicit fresh-versus-resume external-publication operation dispatch.

Phase 288 is a runtime job/workflow-facing routing envelope.  It requires the
caller to choose exactly one already-defined operation: the fresh Phase 285
execution route or the provider-free Phase 287 recovery route.  It owns no
publication, claim, evidence, filesystem, provider, or durable job-state
behavior.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NoReturn

from .external_publication import (
    ExternalPublicationApproval,
    ExternalPublicationError,
    ExternalPublicationPlan,
    ExternalPublicationTarget,
)
from .external_publication_execution import (
    ExternalPublicationExecutionError,
    ExternalPublicationExecutionResult,
    ExternalPublicationTransport,
)
from .external_publication_execution_evidence import (
    ExternalPublicationExecutionEvidenceError,
)
from .external_publication_execution_orchestration import (
    ExternalPublicationExecutionOrchestrationError,
    execute_and_persist_approved_external_publication,
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
    resume_external_publication_reconciliation_closure,
)

_OPERATION_ERROR_MESSAGE = "external publication operation is blocked"
Classification = Literal[
    "request_contract",
    "configuration",
    "fresh_result_contract",
    "resume_result_contract",
    "dependency_error",
]
Phase285Function = Callable[..., object]
Phase287Function = Callable[..., object]


@dataclass(frozen=True)
class ExternalPublicationFreshOperationRequest:
    """Runtime-only request envelope for one explicit fresh operation."""

    execution_evidence_path: Path
    plan_reconciliation_evidence_path: Path
    output_path: Path
    ledger_directory: Path
    plan: ExternalPublicationPlan
    approval: ExternalPublicationApproval
    target: ExternalPublicationTarget
    transport: ExternalPublicationTransport


@dataclass(frozen=True)
class ExternalPublicationResumeOperationRequest:
    """Runtime-only request envelope for one explicit resume operation."""

    ledger_directory: Path
    approval: ExternalPublicationApproval
    execution_evidence_path: Path
    execution_reconciliation_evidence_path: Path


@dataclass(frozen=True)
class ExternalPublicationOperationFailureDetail:
    """Detail-safe classification for one Phase 288 failure."""

    classification: Classification


class ExternalPublicationOperationError(ValueError):
    """Raised when the explicit operation dispatcher cannot safely proceed."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_OPERATION_ERROR_MESSAGE)
        self.detail = ExternalPublicationOperationFailureDetail(classification)


class ExternalPublicationOperationCompatibilityError(ExternalPublicationOperationError):
    """Raised when a Phase 288 request, dependency, or result is incompatible."""


def run_external_publication_operation(
    request: (
        ExternalPublicationFreshOperationRequest
        | ExternalPublicationResumeOperationRequest
    ),
    *,
    phase285_function: Phase285Function = (
        execute_and_persist_approved_external_publication
    ),
    phase287_function: Phase287Function = (
        resume_external_publication_reconciliation_closure
    ),
) -> ExternalPublicationExecutionResult | ExternalPublicationExecutionReconciliation:
    """Run exactly one explicitly selected fresh or resume operation.

    Exact request runtime type selects one lower public boundary.  The other
    dependency is deliberately ignored, and no route inference, retry,
    fallback, compensation, or automatic continuation is attempted.
    """
    if type(request) is ExternalPublicationFreshOperationRequest:
        return _run_fresh(request, phase285_function)
    if type(request) is ExternalPublicationResumeOperationRequest:
        return _run_resume(request, phase287_function)
    _raise_operation("request_contract")


def _run_fresh(
    request: ExternalPublicationFreshOperationRequest,
    phase285_function: Phase285Function,
) -> ExternalPublicationExecutionResult:
    if not callable(phase285_function):
        _raise_operation("configuration")
    try:
        result = phase285_function(
            execution_evidence_path=request.execution_evidence_path,
            reconciliation_evidence_path=request.plan_reconciliation_evidence_path,
            output_path=request.output_path,
            ledger_directory=request.ledger_directory,
            plan=request.plan,
            approval=request.approval,
            target=request.target,
            transport=request.transport,
        )
    except (
        ExternalPublicationExecutionOrchestrationError,
        ExternalPublicationExecutionError,
        ExternalPublicationExecutionEvidenceError,
        ExternalPublicationError,
    ):
        raise
    except Exception:
        _raise_operation("dependency_error")

    _validate_fresh_result(result)
    return result  # type: ignore[return-value]


def _run_resume(
    request: ExternalPublicationResumeOperationRequest,
    phase287_function: Phase287Function,
) -> ExternalPublicationExecutionReconciliation:
    if not callable(phase287_function):
        _raise_operation("configuration")
    try:
        result = phase287_function(
            ledger_directory=request.ledger_directory,
            approval=request.approval,
            execution_evidence_path=request.execution_evidence_path,
            reconciliation_evidence_path=request.execution_reconciliation_evidence_path,
        )
    except (
        ExternalPublicationExecutionReconciliationResumeError,
        ExternalPublicationError,
        ExternalPublicationExecutionReconciliationOrchestrationError,
        ExternalPublicationExecutionReconciliationError,
        ExternalPublicationExecutionReconciliationEvidenceError,
    ):
        raise
    except Exception:
        _raise_operation("dependency_error")

    _validate_resume_result(result)
    return result  # type: ignore[return-value]


def _validate_fresh_result(result: object) -> None:
    if type(result) is not ExternalPublicationExecutionResult:
        _raise_operation("fresh_result_contract")
    try:
        ExternalPublicationExecutionResult(
            schema_version=result.schema_version,  # type: ignore[union-attr]
            regeneration_id=result.regeneration_id,  # type: ignore[union-attr]
            publication_attempt_claim_sha256=(  # type: ignore[union-attr]
                result.publication_attempt_claim_sha256
            ),
            publication_plan_sha256=result.publication_plan_sha256,  # type: ignore[union-attr]
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
        _raise_operation("fresh_result_contract")


def _validate_resume_result(result: object) -> None:
    if type(result) is not ExternalPublicationExecutionReconciliation:
        _raise_operation("resume_result_contract")
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
        _raise_operation("resume_result_contract")


def _raise_operation(classification: Classification) -> NoReturn:
    raise ExternalPublicationOperationCompatibilityError(classification) from None


__all__ = [
    "ExternalPublicationFreshOperationRequest",
    "ExternalPublicationOperationCompatibilityError",
    "ExternalPublicationOperationError",
    "ExternalPublicationOperationFailureDetail",
    "ExternalPublicationResumeOperationRequest",
    "run_external_publication_operation",
]
