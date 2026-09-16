"""Provider-free resumable handoff for external-publication reconciliation.

Phase 287 derives the canonical Phase 280 claim path from the exact caller
approval and ledger directory, then resumes only the Phase 286 closure.  It
owns no claim, evidence, reconciliation, provider, or filesystem operation.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NoReturn

from ai_office.engine.external_publication import (
    ExternalPublicationApproval,
    ExternalPublicationError,
    external_publication_attempt_claim_path,
    external_publication_consumption_key,
)
from ai_office.engine.external_publication_execution_reconciliation import (
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationExecutionReconciliationError,
)
from ai_office.engine.external_publication_execution_reconciliation_evidence import (
    ExternalPublicationExecutionReconciliationEvidenceError,
)
from ai_office.engine.external_publication_execution_reconciliation_orchestration import (  # noqa: E501
    ExternalPublicationExecutionReconciliationOrchestrationError,
    reconcile_and_persist_external_publication_execution,
)

_RESUME_ERROR_MESSAGE = "external publication reconciliation resume is blocked"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
Classification = Literal[
    "configuration",
    "approval_contract",
    "consumption_key_contract",
    "claim_path_contract",
    "closure_contract",
    "dependency_error",
]
Phase280ConsumptionKeyFunction = Callable[[ExternalPublicationApproval], object]
Phase280ClaimPathFunction = Callable[[object, object], object]
Phase286Function = Callable[..., object]


@dataclass(frozen=True)
class ExternalPublicationExecutionReconciliationResumeFailureDetail:
    """Detail-safe classification for one Phase 287 failure."""

    classification: Classification


class ExternalPublicationExecutionReconciliationResumeError(ValueError):
    """Raised when the resumable reconciliation handoff cannot complete."""

    def __init__(self, classification: Classification = "dependency_error") -> None:
        super().__init__(_RESUME_ERROR_MESSAGE)
        self.detail = ExternalPublicationExecutionReconciliationResumeFailureDetail(
            classification
        )


class ExternalPublicationExecutionReconciliationResumeCompatibilityError(
    ExternalPublicationExecutionReconciliationResumeError
):
    """Raised when a Phase 287 boundary contract is incompatible."""


def resume_external_publication_reconciliation_closure(
    *,
    ledger_directory: Path,
    approval: ExternalPublicationApproval,
    execution_evidence_path: Path,
    reconciliation_evidence_path: Path,
    phase280_consumption_key_function: Phase280ConsumptionKeyFunction = (
        external_publication_consumption_key
    ),
    phase280_claim_path_function: Phase280ClaimPathFunction = (
        external_publication_attempt_claim_path
    ),
    phase286_function: Phase286Function = (
        reconcile_and_persist_external_publication_execution
    ),
) -> ExternalPublicationExecutionReconciliation:
    """Resume the provider-free reconciliation closure exactly once.

    The route is deliberately linear: exact approval type validation, one
    Phase 280 consumption-key derivation, one Phase 280 canonical claim-path
    derivation, one Phase 286 closure, exact result validation, and the
    unchanged Phase 286 result object.  Known lower-boundary errors propagate
    unchanged; no retry, fallback, compensation, or fresh execution occurs.
    """
    if type(approval) is not ExternalPublicationApproval:
        _raise_resume("approval_contract")
    if not (
        callable(phase280_consumption_key_function)
        and callable(phase280_claim_path_function)
        and callable(phase286_function)
    ):
        _raise_resume("configuration")

    try:
        consumption_key = phase280_consumption_key_function(approval)
    except ExternalPublicationError:
        raise
    except Exception:
        _raise_resume("dependency_error")
    if not _is_consumption_key(consumption_key):
        _raise_resume("consumption_key_contract")

    try:
        claim_path = phase280_claim_path_function(ledger_directory, consumption_key)
    except ExternalPublicationError:
        raise
    except Exception:
        _raise_resume("dependency_error")
    if type(claim_path) is not _PATH_TYPE:
        _raise_resume("claim_path_contract")

    try:
        reconciliation = phase286_function(
            claim_path=claim_path,
            execution_evidence_path=execution_evidence_path,
            reconciliation_evidence_path=reconciliation_evidence_path,
        )
    except (
        ExternalPublicationExecutionReconciliationOrchestrationError,
        ExternalPublicationExecutionReconciliationError,
        ExternalPublicationExecutionReconciliationEvidenceError,
    ):
        raise
    except Exception:
        _raise_resume("dependency_error")

    _snapshot_and_validate(reconciliation)
    return reconciliation  # type: ignore[return-value]


def _snapshot_and_validate(result: object) -> tuple[object, ...]:
    """Validate all five result fields without replacing the returned object."""
    if type(result) is not ExternalPublicationExecutionReconciliation:
        _raise_resume("closure_contract")
    try:
        schema_version = result.schema_version  # type: ignore[union-attr]
        claim_sha256 = result.claim_sha256  # type: ignore[union-attr]
        execution_evidence_sha256 = (  # type: ignore[union-attr]
            result.execution_evidence_sha256
        )
        status = result.status  # type: ignore[union-attr]
        mismatched_fields = result.mismatched_fields  # type: ignore[union-attr]

        snapshot = (
            schema_version,
            claim_sha256,
            execution_evidence_sha256,
            status,
            mismatched_fields,
        )
        # Revalidate every authoritative field through the existing exact
        # model contract without replacing or returning a new result object.
        ExternalPublicationExecutionReconciliation(
            schema_version=snapshot[0],
            claim_sha256=snapshot[1],
            execution_evidence_sha256=snapshot[2],
            status=snapshot[3],
            mismatched_fields=snapshot[4],
        )
        return snapshot
    except ExternalPublicationExecutionReconciliationResumeError:
        raise
    except Exception:
        _raise_resume("closure_contract")


def _is_consumption_key(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _raise_resume(classification: Classification) -> NoReturn:
    raise ExternalPublicationExecutionReconciliationResumeCompatibilityError(
        classification
    ) from None


__all__ = [
    "ExternalPublicationExecutionReconciliationResumeCompatibilityError",
    "ExternalPublicationExecutionReconciliationResumeError",
    "ExternalPublicationExecutionReconciliationResumeFailureDetail",
    "resume_external_publication_reconciliation_closure",
]
