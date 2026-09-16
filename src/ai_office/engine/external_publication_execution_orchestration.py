"""Phase 285 orchestration for approved external publication.

This is the first higher-level external-publication boundary.  It composes
the public Phase 281 execution boundary with the public Phase 282 execution
evidence boundary and owns no lower-level execution, claim, reconciliation,
or evidence implementation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NoReturn

from ai_office.engine.external_publication import (
    ExternalPublicationApproval,
    ExternalPublicationError,
    ExternalPublicationPlan,
    ExternalPublicationTarget,
)
from ai_office.engine.external_publication_execution import (
    ExternalPublicationExecutionError,
    ExternalPublicationExecutionResult,
    ExternalPublicationTransport,
    execute_approved_external_publication,
)
from ai_office.engine.external_publication_execution_evidence import (
    ExternalPublicationExecutionEvidenceError,
    persist_external_publication_execution_result,
    preflight_external_publication_execution_result_path,
)

_ORCHESTRATION_ERROR_MESSAGE = "external publication execution orchestration is blocked"
Phase282PreflightFunction = Callable[[Path], None]
Phase281Function = Callable[..., ExternalPublicationExecutionResult]
Phase282PersistenceFunction = Callable[[Path, ExternalPublicationExecutionResult], None]

Classification = Literal[
    "configuration",
    "execution_contract",
    "persistence_contract",
    "result_mutation",
    "dependency_error",
]


@dataclass(frozen=True)
class ExternalPublicationExecutionOrchestrationFailureDetail:
    """Detail-safe classification for one Phase 285 failure."""

    classification: Classification


class ExternalPublicationExecutionOrchestrationError(ValueError):
    """Raised when the Phase 285 composition cannot safely complete."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_ORCHESTRATION_ERROR_MESSAGE)
        self.detail = ExternalPublicationExecutionOrchestrationFailureDetail(
            classification
        )


class ExternalPublicationExecutionOrchestrationCompatibilityError(
    ExternalPublicationExecutionOrchestrationError
):
    """Raised when the Phase 285 orchestration contract is incompatible."""


def execute_and_persist_approved_external_publication(
    *,
    execution_evidence_path: Path,
    reconciliation_evidence_path: Path,
    output_path: Path,
    ledger_directory: Path,
    plan: ExternalPublicationPlan,
    approval: ExternalPublicationApproval,
    target: ExternalPublicationTarget,
    transport: ExternalPublicationTransport,
    phase282_preflight_function: Phase282PreflightFunction = (
        preflight_external_publication_execution_result_path
    ),
    phase281_function: Phase281Function = execute_approved_external_publication,
    phase282_persistence_function: Phase282PersistenceFunction = (
        persist_external_publication_execution_result
    ),
) -> ExternalPublicationExecutionResult:
    """Execute one approved publication and persist its exact result once.

    The only route is fresh Phase 282 target preflight, one Phase 281 call,
    one Phase 282 persistence call, and the exact Phase 281 result object.
    Known lower-boundary errors are authoritative and are re-raised by
    identity.  No retry or compensation is attempted after any boundary has
    been crossed.
    """
    if not (
        callable(phase282_preflight_function)
        and callable(phase281_function)
        and callable(phase282_persistence_function)
    ):
        _raise_orchestration("configuration")

    try:
        phase282_preflight_function(execution_evidence_path)
    except ExternalPublicationExecutionEvidenceError:
        raise
    except Exception:
        _raise_orchestration("dependency_error")

    try:
        result = phase281_function(
            reconciliation_evidence_path=reconciliation_evidence_path,
            output_path=output_path,
            ledger_directory=ledger_directory,
            plan=plan,
            approval=approval,
            target=target,
            transport=transport,
        )
    except (ExternalPublicationExecutionError, ExternalPublicationError):
        raise
    except Exception:
        _raise_orchestration("dependency_error")

    if type(result) is not ExternalPublicationExecutionResult:
        _raise_orchestration("execution_contract")
    try:
        before_persistence = _snapshot_result(result)
    except Exception:
        _raise_orchestration("execution_contract")

    try:
        persisted = phase282_persistence_function(execution_evidence_path, result)
    except ExternalPublicationExecutionEvidenceError:
        raise
    except Exception:
        _raise_orchestration("dependency_error")

    if persisted is not None:
        _raise_orchestration("persistence_contract")
    if type(result) is not ExternalPublicationExecutionResult:
        _raise_orchestration("result_mutation")
    try:
        after_persistence = _snapshot_result(result)
        unchanged = after_persistence == before_persistence
    except Exception:
        unchanged = False
    if not unchanged:
        _raise_orchestration("result_mutation")
    return result


def _snapshot_result(result: ExternalPublicationExecutionResult) -> tuple[object, ...]:
    """Capture every result value without invoking a Phase 282 helper."""
    return (
        result.schema_version,
        result.regeneration_id,
        result.publication_attempt_claim_sha256,
        result.publication_plan_sha256,
        result.publication_approval_sha256,
        result.business_output_sha256,
        result.output_byte_length,
        result.provider,
        result.publication_target_sha256,
        result.publication_id,
        result.status,
    )


def _raise_orchestration(classification: Classification) -> NoReturn:
    raise ExternalPublicationExecutionOrchestrationCompatibilityError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationExecutionOrchestrationCompatibilityError",
    "ExternalPublicationExecutionOrchestrationError",
    "ExternalPublicationExecutionOrchestrationFailureDetail",
    "execute_and_persist_approved_external_publication",
]
